"""Thumbs up/down on a search result (one vote per user x search x candidate; last vote wins)."""

from __future__ import annotations

from ..db import Database
from ..errors import not_found, parse_uuid


async def give_feedback(db: Database, owner_id: str, search_id: str, candidate_id: str, rating: int,
                        note: str | None) -> dict:
    sid = parse_uuid(search_id, "Search")
    cid = parse_uuid(candidate_id, "Candidate")
    ok = await db.fetch_one(
        "select (select 1 from search_runs where id = %s and owner_id = %s) as s, "
        "(select 1 from candidates where id = %s and owner_id = %s) as c", (sid, owner_id, cid, owner_id))
    if not ok["s"]:
        raise not_found("Search")
    if not ok["c"]:
        raise not_found("Candidate")
    row = await db.fetch_one(
        "insert into feedback (owner_id, search_id, candidate_id, rating, note) values (%s, %s, %s, %s, %s) "
        "on conflict (owner_id, search_id, candidate_id) do update set rating = excluded.rating, "
        "note = excluded.note, created_at = now() returning *", (owner_id, sid, cid, rating, note))
    return {"id": str(row["id"]), "search_id": str(row["search_id"]), "candidate_id": str(row["candidate_id"]),
            "rating": row["rating"], "note": row["note"], "created_at": row["created_at"]}
