from __future__ import annotations

from fastapi import APIRouter, Depends

from ..adapters.ml import MLAdapter
from ..auth import User, current_user
from ..config import Settings
from ..db import Database
from ..deps import get_db, get_llm_limiter, get_ml, get_settings_dep
from ..ratelimit import RateLimiter
from ..schemas.api import AnalysisRequest, AnalysisResponse
from ..services import analysis as svc

router = APIRouter(tags=["analysis"])


@router.post("/analysis", response_model=AnalysisResponse,
             summary="Skill-gap analysis with verified citations (cached per JD spec x candidate x prompt)")
async def analyse(body: AnalysisRequest, user: User = Depends(current_user), db: Database = Depends(get_db),
                  ml: MLAdapter = Depends(get_ml), limiter: RateLimiter = Depends(get_llm_limiter),
                  settings: Settings = Depends(get_settings_dep)):
    return await svc.analyse(db, ml, limiter, user.id, body.jd_id, body.candidate_id, settings.ANALYSIS_TIMEOUT_S)
