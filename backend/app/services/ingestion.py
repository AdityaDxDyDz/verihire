"""Ingestion orchestrator: the ``documents`` table *is* the queue (survives restarts).

State machine per document::

    uploaded --claim--> extracting --> embedding --> ready
                            |              |
                            +--> needs_ocr +--> failed (error_code, error_message)

* **Atomic claim** - ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)``:
  two workers (or two containers) can never take the same document.
* **Fencing** - every later write checks ``claimed_by = <this worker>``; if the janitor gave the
  document to someone else (or it was deleted) a slow "zombie" worker cannot overwrite it.
* **Idempotent reprocess** - results are written in ONE transaction that replaces the
  candidate's chunks, PII and cached analyses.
* **Heartbeat + janitor** - in-flight docs whose heartbeat is older than INGEST_STALE_AFTER_S go back
  to ``uploaded`` (or ``failed: timeout`` after INGEST_MAX_ATTEMPTS). On startup the sweep reclaims
  docs held by a previous (killed) process immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid

from docint.contracts import ExtractionError

from ..adapters.ml import EmbeddingDimError, MLAdapter
from ..config import Settings
from ..db import Database, vector_literal
from .storage import Storage, StorageError

log = logging.getLogger("verihire.ingest")

INFLIGHT = ("extracting", "embedding")


class _Fenced(Exception):
    """This worker no longer owns the document (deleted or reclaimed)."""


class IngestionService:
    def __init__(self, db: Database, storage: Storage, ml: MLAdapter, settings: Settings):
        self.db = db
        self.storage = storage
        self.ml = ml
        self.s = settings
        self.instance_id = f"{socket.gethostname()[:20]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.sweep(startup=True)
        if not self.s.INGEST_WORKERS_ENABLED:
            return
        self._running = True
        for i in range(self.s.INGEST_CONCURRENCY):
            self._tasks.append(asyncio.create_task(self._worker_loop(f"{self.instance_id}/w{i}"), name=f"ingest-w{i}"))
        self._tasks.append(asyncio.create_task(self._janitor_loop(), name="ingest-janitor"))
        log.info("ingestion started with %d workers", self.s.INGEST_CONCURRENCY)

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------------ queue
    async def claim(self, worker_id: str) -> dict | None:
        return await self.db.fetch_one(
            """
            update documents d
               set status = 'extracting', claimed_by = %s, claimed_at = now(), heartbeat_at = now(),
                   attempts = d.attempts + 1, error_code = null, error_message = null, updated_at = now()
             where d.id = (select id from documents
                            where status = 'uploaded'
                            order by created_at, id
                            for update skip locked
                            limit 1)
            returning d.id, d.owner_id, d.filename, d.storage_path, d.attempts
            """, (worker_id,))

    async def sweep(self, startup: bool = False) -> int:
        """Reclaim stuck in-flight documents. Returns how many were reset."""
        stale = 0.0 if startup and self.s.INGEST_SINGLE_INSTANCE else self.s.INGEST_STALE_AFTER_S
        rows = await self.db.fetch_all(
            """
            update documents
               set status = case when attempts >= %(max)s then 'failed' else 'uploaded' end,
                   error_code = case when attempts >= %(max)s then 'timeout' else null end,
                   error_message = case when attempts >= %(max)s
                                        then 'Processing did not finish after several attempts' else null end,
                   claimed_by = null, heartbeat_at = null, updated_at = now()
             where status in ('extracting', 'embedding')
               and (claimed_by is null or claimed_by not like %(me)s)
               and coalesce(heartbeat_at, claimed_at, updated_at) < now() - make_interval(secs => %(stale)s)
            returning id, status
            """, {"max": self.s.INGEST_MAX_ATTEMPTS, "me": f"{self.instance_id}/%", "stale": stale})
        if rows:
            log.warning("janitor reclaimed %d stuck documents", len(rows))
            self.wake()
        return len(rows)

    async def _worker_loop(self, worker_id: str) -> None:
        while self._running:
            try:
                doc = await self.claim(worker_id)
                if doc:
                    await self.process(doc, worker_id)
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.s.INGEST_POLL_INTERVAL_S)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a worker must never die
                log.exception("worker loop error")
                await asyncio.sleep(1)

    async def _janitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.s.JANITOR_INTERVAL_S)
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("janitor error")

    async def drain(self, concurrency: int = 1) -> int:
        """Process every queued document now and return how many were processed (tests, seed script)."""
        count = 0

        async def run(i: int) -> None:
            nonlocal count
            wid = f"{self.instance_id}/drain{i}"
            while True:
                doc = await self.claim(wid)
                if not doc:
                    return
                await self.process(doc, wid)
                count += 1

        await asyncio.gather(*(run(i) for i in range(max(1, concurrency))))
        return count

    # ------------------------------------------------------------------ processing
    async def _heartbeat(self, doc_id, worker_id: str) -> None:
        while True:
            await asyncio.sleep(self.s.INGEST_HEARTBEAT_S)
            await self.db.execute("update documents set heartbeat_at = now() where id = %s and claimed_by = %s",
                                  (doc_id, worker_id))

    async def _set_status(self, doc_id, worker_id: str, status: str) -> None:
        n = await self.db.execute(
            "update documents set status = %s, heartbeat_at = now(), updated_at = now() "
            "where id = %s and claimed_by = %s and status = any(%s)", (status, doc_id, worker_id, list(INFLIGHT)))
        if n == 0:
            raise _Fenced()

    async def _finish(self, doc_id, worker_id: str, status: str, code: str | None = None, message: str | None = None,
                      page_count: int | None = None) -> None:
        await self.db.execute(
            "update documents set status = %s, error_code = %s, error_message = %s, "
            "page_count = coalesce(%s, page_count), claimed_by = null, heartbeat_at = null, updated_at = now() "
            "where id = %s and claimed_by = %s and status = any(%s)",
            (status, code, (message or "")[:500] or None, page_count, doc_id, worker_id, list(INFLIGHT)))

    async def process(self, doc: dict, worker_id: str) -> str:
        """Run one claimed document through the pipeline. Returns the final status."""
        doc_id = doc["id"]
        hb = asyncio.create_task(self._heartbeat(doc_id, worker_id))
        timeout = self.s.INGEST_TIMEOUT_S
        try:
            pdf = await self.storage.get(doc["storage_path"], self.s.max_pdf_bytes)
            extracted, result, embed_texts = await self.ml.run_ingest(pdf, doc["filename"], timeout)
            if extracted.is_scanned:
                await self._finish(doc_id, worker_id, "needs_ocr", "needs_ocr",
                                   "Scanned PDF - no selectable text (OCR not supported yet)", extracted.page_count)
                return "needs_ocr"
            if not result.chunks:
                await self._finish(doc_id, worker_id, "failed", "no_text", "No readable text found", extracted.page_count)
                return "failed"
            await self._set_status(doc_id, worker_id, "embedding")
            vectors = await asyncio.wait_for(self.ml.embed_documents(embed_texts), timeout)
            await self._write_results(doc, worker_id, extracted, result, vectors)
            return "ready"
        except _Fenced:
            log.warning("document %s no longer owned by %s; dropping result", doc_id, worker_id)
            return "fenced"
        except ExtractionError as e:
            await self._finish(doc_id, worker_id, "failed", e.code, e.message)
        except TimeoutError:
            await self._finish(doc_id, worker_id, "failed", "timeout", f"Processing took longer than {timeout:g} s")
        except StorageError as e:
            await self._finish(doc_id, worker_id, "failed", "storage_error", str(e))
        except EmbeddingDimError as e:
            await self._finish(doc_id, worker_id, "failed", "embedding_dim_mismatch", str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("ingestion failed", extra={"document_id": str(doc_id)})
            await self._finish(doc_id, worker_id, "failed", "internal_error", f"{type(e).__name__}: {e}"[:300])
        finally:
            hb.cancel()
        return "failed"

    def _pipeline_version(self, prof) -> str:
        pv = self.ml.pipeline_version
        if pv and pv != "unknown":
            return pv if not prof.parser_version or prof.parser_version in pv else f"{pv}/{prof.parser_version}"
        return prof.parser_version or "unknown"

    async def _write_results(self, doc: dict, worker_id: str, extracted, result, vectors) -> None:
        doc_id, owner = doc["id"], doc["owner_id"]
        prof = result.profile
        skills = sorted({s.strip().lower() for s in prof.skills if s and s.strip()})
        async with self.db.transaction() as conn:
            cur = await conn.execute("select status, claimed_by from documents where id = %s for update", (doc_id,))
            row = await cur.fetchone()
            if not row or row["claimed_by"] != worker_id or row["status"] not in INFLIGHT:
                raise _Fenced()
            cand_id = uuid.uuid4()
            cur = await conn.execute(
                """
                insert into candidates (id, owner_id, document_id, label, profile, skills, years_experience, education_level)
                values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                on conflict (document_id) do update
                   set profile = excluded.profile, skills = excluded.skills,
                       years_experience = excluded.years_experience, education_level = excluded.education_level
                returning id
                """,
                (cand_id, owner, doc_id, f"Candidate {cand_id.hex[:6].upper()}", prof.model_dump_json(), skills,
                 float(prof.years_experience), int(prof.education_level)))
            cand_id = (await cur.fetchone())["id"]
            await conn.execute("delete from chunks where document_id = %s", (doc_id,))
            await conn.execute("delete from analyses where candidate_id = %s", (cand_id,))
            pii = result.pii
            await conn.execute(
                """
                insert into candidate_pii (candidate_id, full_name, emails, phones, urls, other)
                values (%s, %s, %s, %s, %s, %s::jsonb)
                on conflict (candidate_id) do update
                   set full_name = excluded.full_name, emails = excluded.emails, phones = excluded.phones,
                       urls = excluded.urls, other = excluded.other
                """, (cand_id, pii.full_name, pii.emails, pii.phones, pii.urls, json.dumps(pii.other)))
            cur = conn.cursor()
            await cur.executemany(
                """
                insert into chunks (owner_id, document_id, candidate_id, chunk_index, section, page_no,
                                    char_start, char_end, text_scrubbed, token_count, embedding)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                """,
                [(owner, doc_id, cand_id, c.chunk_index, (c.section or "other")[:40], c.page_no, c.char_start,
                  c.char_end, c.text_scrubbed, c.token_count, vector_literal(v))
                 for c, v in zip(result.chunks, vectors, strict=True)])
            await conn.execute(
                """
                update documents set status = 'ready', page_count = %s, pipeline_version = %s, embedder = %s,
                       suspicious_hidden_text = %s, error_code = null, error_message = null,
                       claimed_by = null, heartbeat_at = null, updated_at = now()
                 where id = %s
                """, (extracted.page_count, self._pipeline_version(prof), self.ml.embedder_name,
                      bool(extracted.suspicious_hidden_text), doc_id))
