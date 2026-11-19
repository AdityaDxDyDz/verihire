"""Accessors for objects created at startup (kept on app.state) - used as FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from .adapters.ml import MLAdapter
from .config import Settings
from .db import Database
from .ratelimit import RateLimiter
from .services.ingestion import IngestionService
from .services.outreach import OutreachService
from .services.search import SearchService
from .services.storage import Storage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_ml(request: Request) -> MLAdapter:
    return request.app.state.ml


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_ingestion(request: Request) -> IngestionService:
    return request.app.state.ingestion


def get_search(request: Request) -> SearchService:
    return request.app.state.search


def get_llm_limiter(request: Request) -> RateLimiter:
    return request.app.state.llm_limiter


def get_outreach(request: Request) -> OutreachService:
    return request.app.state.outreach
