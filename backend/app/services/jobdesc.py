"""Job descriptions: create (parse with the ML layer), list, fetch, save edited spec."""

from __future__ import annotations

import asyncio
import json

from docint.contracts import JobSpec

from ..adapters.ml import MLAdapter
from ..db import Database
from ..errors import AppError, not_found, parse_uuid
from .audit import audit


def normalise_spec(spec: JobSpec, raw_text: str | None = None) -> JobSpec:
    """Lowercase + de-duplicate skills; a skill can't be both required and preferred."""

    def clean(items: list[str]) -> list[str]:
        out: list[str] = []
        for s in items:
            s = " ".join(str(s).split()).lower()[:60]
            if s and s not in out:
                out.append(s)
        return out[:50]

    req = clean(spec.required_skills)
    pref = [s for s in clean(spec.preferred_skills) if s not in req]
    return spec.model_copy(update={
        "required_skills": req,
        "preferred_skills": pref,
        "responsibilities": [" ".join(r.split())[:300] for r in spec.responsibilities if r and r.strip()][:30],
        "raw_text": spec.raw_text or raw_text or "",
        "title": (spec.title or None) and spec.title.strip()[:200],
    })


def _out(row: dict) -> dict:
    spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
    return {"id": str(row["id"]), "title": row["title"], "spec": spec,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


async def create(db: Database, ml: MLAdapter, owner_id: str, title: str | None, text: str, timeout: float) -> tuple[dict, list[str]]:
    try:
        spec, warnings = await asyncio.wait_for(ml.parse_job_description(text), timeout)
    except TimeoutError:
        raise AppError(504, "jd_parse_timeout", "Parsing the job description took too long. Please retry.") from None
    spec = normalise_spec(spec, raw_text=text)
    final_title = (title or spec.title or "Untitled role").strip()[:200]
    spec = spec.model_copy(update={"title": final_title, "raw_text": text})
    row = await db.fetch_one(
        "insert into job_descriptions (owner_id, title, raw_text, spec) values (%s, %s, %s, %s::jsonb) returning *",
        (owner_id, final_title, text, spec.model_dump_json()))
    return _out(row), warnings


async def get_row(db: Database, owner_id: str, jd_id: str) -> dict:
    jid = parse_uuid(jd_id, "Job description")
    row = await db.fetch_one("select * from job_descriptions where id = %s and owner_id = %s", (jid, owner_id))
    if not row:
        raise not_found("Job description")
    return row


async def get(db: Database, owner_id: str, jd_id: str) -> dict:
    return _out(await get_row(db, owner_id, jd_id))


async def list_all(db: Database, owner_id: str, limit: int = 100) -> dict:
    rows = await db.fetch_all(
        "select j.*, (select s.id from search_runs s where s.jd_id = j.id and s.owner_id = j.owner_id "
        "order by s.created_at desc limit 1) as last_search_id "
        "from job_descriptions j where j.owner_id = %s order by j.updated_at desc limit %s", (owner_id, limit))
    items = []
    for r in rows:
        item = _out(r)
        item["required_skills"] = list(item["spec"].get("required_skills", []))
        item["last_search_id"] = str(r["last_search_id"]) if r["last_search_id"] else None
        items.append(item)
    return {"items": items}


async def update(db: Database, owner_id: str, jd_id: str, title: str | None, spec: JobSpec) -> dict:
    row = await get_row(db, owner_id, jd_id)
    spec = normalise_spec(spec, raw_text=row["raw_text"])
    new_title = (title or spec.title or row["title"] or "Untitled role").strip()[:200]
    spec = spec.model_copy(update={"title": new_title})
    row = await db.fetch_one(
        "update job_descriptions set title = %s, spec = %s::jsonb, updated_at = now() "
        "where id = %s and owner_id = %s returning *", (new_title, spec.model_dump_json(), row["id"], owner_id))
    return _out(row)


async def delete(db: Database, owner_id: str, jd_id: str) -> None:
    """Delete a JD. Cascades: analyses, interview invitations and replies. Past search runs are kept with jd_id = null."""
    row = await get_row(db, owner_id, jd_id)
    async with db.transaction() as conn:
        await conn.execute("delete from job_descriptions where id = %s and owner_id = %s", (row["id"], owner_id))
        await audit(db, owner_id, "jd_delete", "job_description", str(row["id"]), {"title": row["title"]}, conn=conn)


def spec_from_row(row: dict) -> JobSpec:
    spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
    return JobSpec.model_validate(spec)
