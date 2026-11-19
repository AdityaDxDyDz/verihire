from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import User, current_user
from ..db import Database
from ..deps import get_db
from ..schemas.api import CandidateResponse, FeedbackRequest, FeedbackResponse
from ..services import candidates as svc
from ..services import feedback as fb

router = APIRouter(tags=["candidates"])


@router.get("/candidates/{candidate_id}", response_model=CandidateResponse,
            summary="Candidate profile; reveal=true returns PII and writes an audit row")
async def get_candidate(candidate_id: str, reveal: bool = Query(False), user: User = Depends(current_user),
                        db: Database = Depends(get_db)):
    return await svc.get_candidate(db, user.id, candidate_id, reveal)


@router.post("/feedback", response_model=FeedbackResponse, status_code=201)
async def feedback(body: FeedbackRequest, user: User = Depends(current_user), db: Database = Depends(get_db)):
    return await fb.give_feedback(db, user.id, body.search_id, body.candidate_id, body.rating, body.note)
