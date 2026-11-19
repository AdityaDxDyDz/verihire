from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..adapters.ml import MLAdapter
from ..auth import User, current_user
from ..config import Settings
from ..db import Database
from ..deps import get_db, get_llm_limiter, get_ml, get_settings_dep
from ..ratelimit import RateLimiter
from ..schemas.api import JobDescriptionCreate, JobDescriptionListResponse, JobDescriptionResponse, JobDescriptionUpdate
from ..services import jobdesc as svc

router = APIRouter(tags=["job descriptions"])
log = logging.getLogger("verihire.jd")


@router.post("/job-descriptions", response_model=JobDescriptionResponse, status_code=201,
             summary="Create a JD and parse it into an editable spec")
async def create(body: JobDescriptionCreate, user: User = Depends(current_user), db: Database = Depends(get_db),
                 ml: MLAdapter = Depends(get_ml), limiter: RateLimiter = Depends(get_llm_limiter),
                 settings: Settings = Depends(get_settings_dep)):
    limiter.check(user.id)
    out, warnings = await svc.create(db, ml, user.id, body.title, body.text, settings.JD_PARSE_TIMEOUT_S)
    if warnings:
        log.warning("jd created with warnings %s", warnings)
    return out


@router.get("/job-descriptions", response_model=JobDescriptionListResponse)
async def list_all(user: User = Depends(current_user), db: Database = Depends(get_db)):
    return await svc.list_all(db, user.id)


@router.get("/job-descriptions/{jd_id}", response_model=JobDescriptionResponse)
async def get(jd_id: str, user: User = Depends(current_user), db: Database = Depends(get_db)):
    return await svc.get(db, user.id, jd_id)


@router.put("/job-descriptions/{jd_id}", response_model=JobDescriptionResponse, summary="Save the edited spec")
async def update(jd_id: str, body: JobDescriptionUpdate, user: User = Depends(current_user),
                 db: Database = Depends(get_db)):
    return await svc.update(db, user.id, jd_id, body.title, body.spec)
