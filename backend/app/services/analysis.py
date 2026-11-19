"""Skill-gap analysis with backend re-verification of every citation, cached per
(owner, JD, candidate, prompt version, hash of the *current* JD spec) - editing the JD invalidates it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from docint.contracts import ResumeProfile, RetrievedChunk, SkillGapResult

from ..adapters.ml import MLAdapter
from ..db import Database
from ..errors import AppError, not_found, parse_uuid
from ..ratelimit import RateLimiter
from . import jobdesc
from .citations import ChunkRef, verify_and_resolve

log = logging.getLogger("verihire.analysis")


def spec_hash(spec) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(), sort_keys=True).encode()).hexdigest()[:32]


async def analyse(db: Database, ml: MLAdapter, limiter: RateLimiter, owner_id: str, jd_id: str,
                  candidate_id: str, timeout: float) -> dict:
    jd_row = await jobdesc.get_row(db, owner_id, jd_id)
    spec = jobdesc.spec_from_row(jd_row)
    cid = parse_uuid(candidate_id, "Candidate")
    cand = await db.fetch_one("select id, profile from candidates where id = %s and owner_id = %s", (cid, owner_id))
    if not cand:
        raise not_found("Candidate")
    pv, sh = ml.prompt_version, spec_hash(spec)

    cached = await db.fetch_one(
        "select result, citations_resolved from analyses where owner_id = %s and jd_id = %s and candidate_id = %s "
        "and prompt_version = %s and spec_hash = %s", (owner_id, jd_row["id"], cid, pv, sh))
    if cached:
        return {"result": cached["result"], "citations_resolved": cached["citations_resolved"], "from_cache": True}

    limiter.check(owner_id)  # only real LLM work counts against the limit
    rows = await db.fetch_all(
        "select id, document_id, chunk_index, section, page_no, text_scrubbed from chunks "
        "where candidate_id = %s and owner_id = %s order by page_no, chunk_index", (cid, owner_id))
    if not rows:
        raise AppError(409, "candidate_not_ready", "This candidate has no processed text yet")
    chunks = [RetrievedChunk(chunk_id=str(r["id"]), document_id=str(r["document_id"]), candidate_id=str(cid),
                             chunk_index=r["chunk_index"], section=r["section"], page_no=r["page_no"],
                             text_scrubbed=r["text_scrubbed"]) for r in rows]
    profile = ResumeProfile.model_validate(cand["profile"])
    try:
        raw: SkillGapResult = await asyncio.wait_for(ml.analyze_skill_gap(spec, profile, chunks), timeout)
    except TimeoutError:
        raise AppError(504, "analysis_timeout", "The analysis took too long. Please retry.") from None
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("skill-gap analysis failed")
        raise AppError(502, "analysis_failed", "The analysis service failed. Please retry.") from e

    refs = {c.chunk_id: ChunkRef(c.chunk_id, c.text_scrubbed, c.page_no, c.section) for c in chunks}
    clean, resolved = verify_and_resolve(raw, refs)
    if clean.dropped_unverified_claims > raw.dropped_unverified_claims:
        log.warning("dropped %d unverified citations", clean.dropped_unverified_claims - raw.dropped_unverified_claims)
    result = clean.model_dump()
    await db.execute(
        "insert into analyses (owner_id, jd_id, candidate_id, prompt_version, spec_hash, result, citations_resolved) "
        "values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) "
        "on conflict (owner_id, jd_id, candidate_id, prompt_version, spec_hash) do update "
        "set result = excluded.result, citations_resolved = excluded.citations_resolved, created_at = now()",
        (owner_id, jd_row["id"], cid, pv, sh, json.dumps(result), json.dumps(resolved)))
    return {"result": result, "citations_resolved": resolved, "from_cache": False}
