"""Uploads, library, jobs, deletion, retry, PDF links."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass

from fastapi import UploadFile

from ..db import Database
from ..errors import AppError, not_found, parse_uuid
from .audit import audit
from .storage import Storage, StorageError, make_key

log = logging.getLogger("verihire.documents")

DOC_STATUSES = ("uploaded", "extracting", "embedding", "ready", "failed", "needs_ocr")
RETRYABLE = ("failed", "needs_ocr", "ready")
SUPPORTED_DOC_TYPES = ("resume",)


def safe_filename(name: str | None) -> str:
    """Display name only (never used as a storage path)."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "unnamed.pdf"
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:190] + dot + ext[:8]) if dot else name[:200]
    return name


def looks_like_pdf(head: bytes) -> bool:
    # the PDF spec allows a little junk before the header; real files almost always start with it
    return b"%PDF-" in head[:1024]


@dataclass
class _Accepted:
    id: str
    filename: str
    data: bytes
    sha: str
    key: str


async def upload(db: Database, storage: Storage, owner_id: str, files: list[UploadFile], doc_type: str,
                 max_bytes: int, max_files: int) -> dict:
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise AppError(400, "unsupported_doc_type", f"doc_type must be one of {', '.join(SUPPORTED_DOC_TYPES)}")
    if not files:
        raise AppError(400, "no_files", "Attach at least one PDF in the 'files' field")
    if len(files) > max_files:
        raise AppError(413, "too_many_files", f"At most {max_files} files per upload")

    rejected: list[dict] = []
    accepted: list[_Accepted] = []
    seen: set[str] = set()
    mb = max_bytes / (1024 * 1024)
    for f in files:
        name = safe_filename(f.filename)
        data = await f.read(max_bytes + 1)
        await f.close()
        if not data:
            rejected.append({"filename": name, "reason": "empty_file", "message": "File is empty"})
        elif len(data) > max_bytes:
            rejected.append({"filename": name, "reason": "too_large", "message": f"File is larger than {mb:g} MB"})
        elif not looks_like_pdf(data):
            rejected.append({"filename": name, "reason": "not_pdf", "message": "File is not a PDF"})
        else:
            sha = hashlib.sha256(data).hexdigest()
            if sha in seen:
                rejected.append({"filename": name, "reason": "duplicate", "message": "Same file appears twice in this upload"})
                continue
            seen.add(sha)
            did = str(uuid.uuid4())
            accepted.append(_Accepted(did, name, data, sha, make_key(owner_id, did)))

    if accepted:  # already in the library?
        rows = await db.fetch_all(
            "select content_sha256 from documents where owner_id = %s and content_sha256 = any(%s)",
            (owner_id, [a.sha for a in accepted]))
        existing = {r["content_sha256"] for r in rows}
        keep = []
        for a in accepted:
            if a.sha in existing:
                rejected.append({"filename": a.filename, "reason": "duplicate", "message": "Already in your library"})
            else:
                keep.append(a)
        accepted = keep

    stored: list[_Accepted] = []
    for a in accepted:
        try:
            await storage.put(a.key, a.data)
            stored.append(a)
        except StorageError:
            log.exception("storage put failed")
            rejected.append({"filename": a.filename, "reason": "storage_error", "message": "Could not store file, retry"})

    if not stored:
        return {"job_id": None, "document_ids": [], "rejected": rejected}

    job_id = str(uuid.uuid4())
    inserted: list[str] = []
    async with db.transaction() as conn:
        await conn.execute("insert into ingestion_jobs (id, owner_id, kind) values (%s, %s, 'upload')", (job_id, owner_id))
        for a in stored:
            cur = await conn.execute(
                "insert into documents (id, owner_id, filename, doc_type, storage_path, size_bytes, content_sha256) "
                "values (%s, %s, %s, %s, %s, %s, %s) on conflict (owner_id, content_sha256) do nothing returning id",
                (a.id, owner_id, a.filename, doc_type, a.key, len(a.data), a.sha))
            if await cur.fetchone():
                inserted.append(a.id)
                await conn.execute("insert into job_documents (job_id, document_id) values (%s, %s)", (job_id, a.id))
            else:  # lost a race with a concurrent identical upload
                rejected.append({"filename": a.filename, "reason": "duplicate", "message": "Already in your library"})
        if not inserted:
            await conn.execute("delete from ingestion_jobs where id = %s", (job_id,))
    for a in stored:
        if a.id not in inserted:
            try:
                await storage.delete(a.key)
            except StorageError:
                log.warning("could not delete orphan object %s", a.key)
    return {"job_id": job_id if inserted else None, "document_ids": inserted, "rejected": rejected}


FINAL = ("ready", "failed", "needs_ocr")
PROCESSING = ("uploaded", "extracting", "embedding")


def _job_status(docs: list[dict]) -> str:
    states = [d["status"] for d in docs]
    if not states or any(s not in FINAL for s in states):
        return "running"  # queued documents count as running: the UI polls until the job is final
    return "completed_with_errors" if any(s != "ready" for s in states) else "completed"


async def get_job(db: Database, owner_id: str, job_id: str) -> dict:
    jid = parse_uuid(job_id, "Job")
    job = await db.fetch_one("select id, created_at from ingestion_jobs where id = %s and owner_id = %s", (jid, owner_id))
    if not job:
        raise not_found("Job")
    docs = await db.fetch_all(
        "select d.id, d.filename, d.status, d.error_message, d.updated_at from job_documents jd "
        "join documents d on d.id = jd.document_id where jd.job_id = %s and d.owner_id = %s order by d.created_at, d.filename",
        (jid, owner_id))
    status = _job_status(docs)
    return {
        "id": str(job["id"]),
        "status": status,
        "created_at": job["created_at"],
        "finished_at": max((d["updated_at"] for d in docs), default=None) if status != "running" else None,
        "total": len(docs),
        "done": sum(d["status"] == "ready" for d in docs),
        "failed": sum(d["status"] in ("failed", "needs_ocr") for d in docs),
        "documents": [{"id": str(d["id"]), "filename": d["filename"], "status": d["status"], "error": d["error_message"]}
                      for d in docs],
    }


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_documents(db: Database, owner_id: str, status: str | None, q: str | None, page: int, page_size: int) -> dict:
    if status is not None and status not in (*DOC_STATUSES, "processing", "all"):
        raise AppError(422, "validation_error", f"status must be one of {', '.join(DOC_STATUSES)}, processing, all")
    where = ["d.owner_id = %s"]
    params: list = [owner_id]
    if status == "processing":
        where.append("d.status = any(%s)")
        params.append(list(PROCESSING))
    elif status and status != "all":
        where.append("d.status = %s")
        params.append(status)
    if q:
        where.append("d.filename ilike %s escape '\\'")
        params.append(f"%{_like_escape(q.strip()[:100])}%")
    w = " and ".join(where)
    total = (await db.fetch_one(f"select count(*) as n from documents d where {w}", params))["n"]
    rows = await db.fetch_all(
        f"select d.*, c.id as candidate_id, c.years_experience as cand_years, c.education_level as cand_edu, "
        f"c.skills as cand_skills from documents d left join candidates c on c.document_id = d.id "
        f"where {w} order by d.created_at desc, d.id limit %s offset %s",
        [*params, page_size, (page - 1) * page_size])
    counts_rows = await db.fetch_all("select status, count(*) as n from documents where owner_id = %s group by status", (owner_id,))
    counts = {s: 0 for s in DOC_STATUSES}
    counts.update({r["status"]: r["n"] for r in counts_rows})
    counts["all"] = sum(counts[s] for s in DOC_STATUSES)
    counts["processing"] = sum(counts[s] for s in PROCESSING)
    return {
        "items": [{
            "id": str(r["id"]), "filename": r["filename"], "doc_type": r["doc_type"], "status": r["status"],
            "size_bytes": r["size_bytes"], "page_count": r["page_count"], "error": r["error_message"],
            "error_code": r["error_code"], "candidate_id": str(r["candidate_id"]) if r["candidate_id"] else None,
            "suspicious_hidden_text": r["suspicious_hidden_text"], "created_at": r["created_at"], "updated_at": r["updated_at"],
            "years_experience": r["cand_years"], "education_level": r["cand_edu"], "skills": list(r["cand_skills"] or []),
        } for r in rows],
        "total": total, "page": page, "page_size": page_size, "counts": counts,
    }


async def _owned_doc(db: Database, owner_id: str, document_id: str) -> dict:
    did = parse_uuid(document_id, "Document")
    row = await db.fetch_one("select * from documents where id = %s and owner_id = %s", (did, owner_id))
    if not row:
        raise not_found("Document")
    return row


async def pdf_url(db: Database, storage: Storage, owner_id: str, document_id: str, ttl: int) -> dict:
    doc = await _owned_doc(db, owner_id, document_id)
    try:
        url = await storage.signed_url(doc["storage_path"], ttl)
    except StorageError as e:
        raise AppError(502, "storage_error", "Could not create a link to the file") from e
    await audit(db, owner_id, "pdf_url", "document", str(doc["id"]), {"ttl": ttl})
    return {"url": url, "expires_in": ttl}


async def delete_document(db: Database, storage: Storage, owner_id: str, document_id: str) -> None:
    doc = await _owned_doc(db, owner_id, document_id)
    async with db.transaction() as conn:
        # cascades: candidates -> candidate_pii, chunks, analyses, feedback; job_documents
        await conn.execute("delete from documents where id = %s and owner_id = %s", (doc["id"], owner_id))
        await audit(db, owner_id, "document_delete", "document", str(doc["id"]), {"filename": doc["filename"]}, conn=conn)
    try:
        await storage.delete(doc["storage_path"])
    except StorageError:
        log.warning("stored file for %s could not be deleted; janitor can retry", doc["id"])


async def retry(db: Database, owner_id: str, document_ids: list[str]) -> dict:
    ids = []
    for d in document_ids:
        try:
            ids.append(uuid.UUID(str(d)))
        except ValueError:
            continue
    if not ids:
        return {"job_id": None, "document_ids": []}
    job_id = str(uuid.uuid4())
    async with db.transaction() as conn:
        cur = await conn.execute(
            "update documents set status = 'uploaded', attempts = 0, error_code = null, error_message = null, "
            "claimed_by = null, claimed_at = null, heartbeat_at = null, updated_at = now() "
            "where owner_id = %s and id = any(%s) and status = any(%s) returning id",
            (owner_id, ids, list(RETRYABLE)))
        retried = [str(r["id"]) for r in await cur.fetchall()]
        if not retried:
            return {"job_id": None, "document_ids": []}
        await conn.execute("insert into ingestion_jobs (id, owner_id, kind) values (%s, %s, 'retry')", (job_id, owner_id))
        cur = conn.cursor()
        await cur.executemany("insert into job_documents (job_id, document_id) values (%s, %s)",
                              [(job_id, r) for r in retried])
    return {"job_id": job_id, "document_ids": retried}
