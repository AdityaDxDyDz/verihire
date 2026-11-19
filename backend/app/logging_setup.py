"""JSON logging + request-id middleware + basic security headers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in ("method", "path", "status", "duration_ms", "user", "document_id", "job_id"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_verihire", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler._verihire = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True  # we log requests ourselves


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns X-Request-ID, logs one line per request, adds security headers."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID_OK.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            if request.url.path.startswith("/api/"):
                response.headers.setdefault("Cache-Control", "no-store")
            return response
        finally:
            if request.url.path != "/api/v1/health":
                logging.getLogger("verihire.http").info(
                    "request", extra={"method": request.method, "path": request.url.path, "status": status,
                                      "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                                      "user": getattr(request.state, "user_id", None)})
            request_id_var.reset(token)
