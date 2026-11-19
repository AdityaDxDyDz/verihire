from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile

from docint.contracts import ExtractionError
from docint.pipeline import extract_uploaded_text

from ..adapters.ml import MLAdapter
from ..auth import User, current_user
from ..config import Settings
from ..db import Database
from ..deps import get_db, get_llm_limiter, get_ml, get_settings_dep
from ..errors import AppError
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


@router.post("/job-descriptions/upload", response_model=JobDescriptionResponse, status_code=201,
             summary="Upload and parse a PDF, DOCX, or TXT job description")
async def create_from_file(file: UploadFile = File(...), title: str | None = Form(default=None),
                           user: User = Depends(current_user), db: Database = Depends(get_db),
                           ml: MLAdapter = Depends(get_ml), limiter: RateLimiter = Depends(get_llm_limiter),
                           settings: Settings = Depends(get_settings_dep)):
    limiter.check(user.id)
    filename = file.filename or ""
    if not filename.lower().endswith((".pdf", ".docx", ".txt")):
        raise AppError(415, "unsupported_file_type", "Upload a PDF, DOCX, or TXT file.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise AppError(413, "file_too_large", "The job description file must be 10 MB or smaller.")
    try:
        text, _file_type = extract_uploaded_text(filename, content)
    except ExtractionError as exc:
        raise AppError(422, exc.code, exc.message) from exc
    if len(text) < 40:
        raise AppError(422, "jd_text_too_short", "The extracted job description must contain at least 40 characters.")
    if len(text) > 20_000:
        raise AppError(413, "jd_text_too_long", "Extracted job description text exceeds 20,000 characters.")
    out, warnings = await svc.create(db, ml, user.id, title, text, settings.JD_PARSE_TIMEOUT_S)
    if warnings:
        log.warning("jd uploaded with warnings %s", warnings)
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
