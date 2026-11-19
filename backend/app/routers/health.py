from __future__ import annotations

from fastapi import APIRouter, Request

from docint.contracts import CONTRACT_VERSION

from ..config import API_VERSION
from ..schemas.api import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    st = request.app.state
    db_ok = await st.db.ping()
    ml = st.ml
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "down",
        "embedder": ml.embedder_name,
        "reranker": ml.reranker_version,
        "contract_version": CONTRACT_VERSION,
        "ml_mode": ml.mode,
        "ml_components": ml.components,
        "stubs_in_use": ml.stubs_in_use,
        "auth_mode": st.settings.AUTH_MODE,
        "storage_mode": st.settings.STORAGE_MODE,
        "app_env": st.settings.APP_ENV,
        "api_version": API_VERSION,
        "prompt_version": ml.prompt_version,
        "pipeline_version": ml.pipeline_version,
        "llm_model": ml.llm_model,
    }
