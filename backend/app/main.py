"""VeriHire API - app factory.

Run locally (from the repo root):
    uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --reload
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .adapters.ml import MLAdapter
from .auth import Authenticator
from .config import API_VERSION, REPO_ROOT, Settings, get_settings
from .db import Database
from .errors import envelope, install_error_handlers
from .logging_setup import RequestContextMiddleware, setup_logging
from .ratelimit import RateLimiter
from .routers import analysis, candidates, documents, health, job_descriptions, me, search
from .services.ingestion import IngestionService
from .services.search import SearchService
from .services.storage import Storage, build_storage

log = logging.getLogger("verihire")
API_PREFIX = "/api/v1"


def _run_migrations(url: str) -> list[str]:
    """Apply db/migrations via scripts/migrate.py (used when AUTO_MIGRATE=true)."""
    mod = sys.modules.get("verihire_migrate")
    if mod is None:
        spec = importlib.util.spec_from_file_location("verihire_migrate", REPO_ROOT / "scripts" / "migrate.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("scripts/migrate.py not found")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["verihire_migrate"] = mod  # must be registered before exec (dataclasses look it up)
        spec.loader.exec_module(mod)
    return mod.migrate(url, verbose=False)


def create_app(settings: Settings | None = None, *, ml: MLAdapter | None = None, storage: Storage | None = None,
               authenticator: Authenticator | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings / ML adapter / storage / authenticator."""
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    settings.validate_for_production()  # refuse to boot with unsafe production config

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        if settings.AUTO_MIGRATE:
            await asyncio.to_thread(_run_migrations, settings.DATABASE_URL)
        st.ml = ml or await asyncio.to_thread(MLAdapter.load, settings.ML_MODE, settings.EMBEDDING_DIM)
        st.db = Database(settings.DATABASE_URL, settings.DB_POOL_MIN, settings.DB_POOL_MAX)
        await st.db.open()
        row = await st.db.fetch_one("select to_regclass('public.documents') is not null as ok")
        if not (row and row["ok"]):
            await st.db.close()
            raise RuntimeError(
                "The database has no VeriHire tables yet. Create them once with "
                "`python scripts/migrate.py --local-shim` (local / Docker Postgres) or set AUTO_MIGRATE=true in .env "
                "(safe: idempotent), then start the server again.")
        st.storage = storage or build_storage(settings)
        st.auth = authenticator or Authenticator(settings)
        st.llm_limiter = RateLimiter(settings.LLM_RATE_LIMIT_PER_MIN)
        st.search = SearchService(st.db, st.ml, settings)
        st.ingestion = IngestionService(st.db, st.storage, st.ml, settings)
        await st.ingestion.start()
        log.info("VeriHire API ready (env=%s auth=%s storage=%s ml=%s components=%s)", settings.APP_ENV,
                 settings.AUTH_MODE, settings.STORAGE_MODE, settings.ML_MODE, st.ml.components)
        try:
            yield
        finally:
            await st.ingestion.stop()
            await st.auth.close()
            await st.storage.close()
            await st.db.close()

    app = FastAPI(title="VeriHire API", version=API_VERSION, lifespan=lifespan,
                  description="Evidence-verified resume screening. All routes need `Authorization: Bearer <token>` "
                              "except `/api/v1/health`. In dev use `dev:you@example.com`.",
                  docs_url="/docs", redoc_url=None, openapi_url="/openapi.json")
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    api = APIRouter(prefix=API_PREFIX)
    for r in (health, me, documents, job_descriptions, search, analysis, candidates):
        api.include_router(r.router)

    @api.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
    async def api_not_found(path: str):
        return JSONResponse(envelope("not_found", f"No API route /{path}"), status_code=404)

    app.include_router(api)

    @app.get("/config.js", include_in_schema=False)
    async def frontend_config():
        """Runtime config for the static frontend, generated from the backend's own settings (public values only:
        the Supabase anon key is public by design; the service key is never exposed)."""
        cfg = {"mode": "supabase" if settings.AUTH_MODE == "supabase" else "dev", "apiBaseUrl": "",
               "supabaseUrl": settings.SUPABASE_URL if settings.AUTH_MODE == "supabase" else "",
               "supabaseAnonKey": settings.SUPABASE_ANON_KEY if settings.AUTH_MODE == "supabase" else ""}
        body = "// generated by the backend from its settings - do not edit\nwindow.VERIHIRE_CONFIG = " + json.dumps(cfg) + ";\n"
        return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    frontend = Path(settings.FRONTEND_DIR)
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        async def root(_: Request):
            return {"name": "VeriHire API", "docs": "/docs", "health": f"{API_PREFIX}/health"}

    return app


def _lazy_app() -> FastAPI:
    return create_app()


# `uvicorn app.main:app` - created at import so settings errors surface immediately
app = _lazy_app()
