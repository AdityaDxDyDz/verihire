"""Uniform error envelope: {"error": {"code": "<snake_case>", "message": "<human readable>"}}."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("verihire.errors")

_STATUS_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 413: "payload_too_large", 415: "unsupported_media_type", 422: "validation_error",
    429: "rate_limited", 500: "internal_error", 502: "upstream_error", 503: "unavailable", 504: "timeout",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        super().__init__(f"{status} {code}: {message}")


def not_found(what: str = "Resource") -> AppError:
    # 404 (not 403) for other tenants' objects: never leak that something exists
    return AppError(404, "not_found", f"{what} not found")


def parse_uuid(value: str, what: str = "Resource") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise not_found(what) from None


def envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _summarise_validation(exc: RequestValidationError) -> str:
    parts = []
    for e in exc.errors()[:5]:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path"))
        parts.append(f"{loc or 'request'}: {e.get('msg', 'invalid')}")
    return "; ".join(parts) or "Invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(envelope(exc.code, exc.message), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "error")
        msg = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return JSONResponse(envelope(code, msg), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(envelope("validation_error", _summarise_validation(exc)), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(envelope("internal_error", "Something went wrong. Please retry."), status_code=500)
