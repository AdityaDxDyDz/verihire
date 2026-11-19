"""Append-only audit trail (PII reveals, PDF access, deletions)."""

from __future__ import annotations

import json

from ..db import Database
from ..logging_setup import request_id_var


async def audit(db: Database, owner_id: str, action: str, target_type: str | None = None,
                target_id: str | None = None, details: dict | None = None, conn=None) -> None:
    sql = ("insert into audit_log (owner_id, action, target_type, target_id, details, request_id) "
           "values (%s, %s, %s, %s, %s::jsonb, %s)")
    params = (owner_id, action, target_type, target_id, json.dumps(details or {}), request_id_var.get())
    if conn is not None:
        await conn.execute(sql, params)
    else:
        await db.execute(sql, params)
