"""Interview-availability e-mails (v1.4).

Recruiter (Bearer token):   POST /outreach/interview-requests          send to shortlisted candidates
                            GET  /outreach/interview-requests          per-candidate status + replies (filter by JD)
                            GET  /outreach/batches/{batch_id}          progress of one send
Candidate (no login, token): GET  /public/interview/{token}            what the reply page shows
                            POST /public/interview/{token}            submit preferred slots
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import User, current_user
from ..deps import get_outreach
from ..schemas.api import (
    InterviewReplyCreate,
    InterviewReplyResponse,
    InterviewRequestBatchResponse,
    InterviewRequestCreate,
    OutreachBatchResponse,
    OutreachListResponse,
    PublicInvitationResponse,
)
from ..services.outreach import OutreachService

router = APIRouter(tags=["outreach"])


@router.post("/outreach/interview-requests", response_model=InterviewRequestBatchResponse, status_code=202,
             summary="E-mail shortlisted candidates asking for preferred interview dates/times")
async def create_interview_requests(body: InterviewRequestCreate, request: Request,
                                    user: User = Depends(current_user),
                                    svc: OutreachService = Depends(get_outreach)):
    """Candidates without an e-mail address in their resume are skipped quietly (`skipped_no_email`); candidates
    already invited for this JD are skipped (`already_invited`) unless `resend` is true. Sending happens in the
    background: poll `GET /outreach/batches/{batch_id}` for progress."""
    request.app.state.outreach_limiter.check(f"outreach:{user.id}")
    return await svc.create_interview_requests(user.id, body.jd_id, body.candidate_ids, body.message,
                                               body.company_name, body.reply_by, body.resend)


@router.get("/outreach/interview-requests", response_model=OutreachListResponse,
            summary="Invitations sent (latest first) with delivery status and the candidate's latest reply")
async def list_interview_requests(jd_id: str | None = Query(None),
                                  status: Literal["queued", "sending", "sent", "failed", "replied"] | None = Query(None),
                                  user: User = Depends(current_user), svc: OutreachService = Depends(get_outreach)):
    return await svc.list_requests(user.id, jd_id, status)


@router.get("/outreach/batches/{batch_id}", response_model=OutreachBatchResponse, summary="Progress of one send")
async def get_batch(batch_id: str, user: User = Depends(current_user), svc: OutreachService = Depends(get_outreach)):
    return await svc.get_batch(user.id, batch_id)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")


@router.get("/public/interview/{token}", response_model=PublicInvitationResponse,
            summary="(no login) Invitation details for the candidate's reply page")
async def public_invitation(token: str, request: Request, response: Response,
                            svc: OutreachService = Depends(get_outreach)):
    request.app.state.public_limiter.check(f"public:{_client_ip(request)}")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return await svc.public_get(token)


@router.post("/public/interview/{token}", response_model=InterviewReplyResponse, status_code=201,
             summary="(no login) Candidate submits 1-5 preferred interview slots")
async def public_reply(token: str, body: InterviewReplyCreate, request: Request, response: Response,
                       svc: OutreachService = Depends(get_outreach)):
    request.app.state.public_limiter.check(f"public:{_client_ip(request)}")
    response.headers["Cache-Control"] = "no-store"
    return await svc.public_reply(token, [s.model_dump() for s in body.slots], body.timezone, body.note)
