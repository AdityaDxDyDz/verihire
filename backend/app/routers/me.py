from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import User, current_user
from ..db import Database
from ..deps import get_db
from ..schemas.api import AuditListResponse, MeResponse
from ..services import candidates as svc

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}


@router.get("/audit", response_model=AuditListResponse, summary="My audit trail (PII reveals, PDF access, deletes)")
async def my_audit(limit: int = Query(50, ge=1, le=500), user: User = Depends(current_user),
                   db: Database = Depends(get_db)):
    return await svc.list_audit(db, user.id, limit)
