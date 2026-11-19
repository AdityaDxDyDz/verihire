"""Candidate profile (blind by default) and audited PII reveal."""

from __future__ import annotations

from ..db import Database
from ..errors import not_found, parse_uuid
from .audit import audit


async def get_candidate(db: Database, owner_id: str, candidate_id: str, reveal: bool) -> dict:
    cid = parse_uuid(candidate_id, "Candidate")
    row = await db.fetch_one(
        "select c.id, c.label, c.document_id, c.profile, d.suspicious_hidden_text from candidates c "
        "join documents d on d.id = c.document_id where c.id = %s and c.owner_id = %s", (cid, owner_id))
    if not row:
        raise not_found("Candidate")
    prof = row["profile"] or {}
    out = {"id": str(row["id"]), "label": row["label"], "document_id": str(row["document_id"]),
           "profile": prof, "suspicious_hidden_text": row["suspicious_hidden_text"], "pii": None,
           "years_experience": prof.get("years_experience"), "education_level": prof.get("education_level")}
    if reveal:
        pii = await db.fetch_one("select full_name, emails, phones, urls from candidate_pii where candidate_id = %s", (cid,))
        p = dict(pii) if pii else {"full_name": None, "emails": [], "phones": [], "urls": []}
        p["email"] = (p.get("emails") or [None])[0]
        p["phone"] = (p.get("phones") or [None])[0]
        out["pii"] = p
        await audit(db, owner_id, "pii_reveal", "candidate", str(cid), {"label": row["label"]})
    return out


async def list_audit(db: Database, owner_id: str, limit: int) -> dict:
    rows = await db.fetch_all(
        "select id, action, target_type, target_id, details, created_at from audit_log "
        "where owner_id = %s order by id desc limit %s", (owner_id, limit))
    return {"items": rows}
