"""Authentication.

* ``AUTH_MODE=dev``: ``Authorization: Bearer dev:<email>`` (local + tests only; refused in production).
* ``AUTH_MODE=supabase``: the Supabase access token is verified by calling
  ``GET {SUPABASE_URL}/auth/v1/user`` with the anon ``apikey``. This works whatever JWT signing
  method the project uses (HS256 secret or asymmetric keys) and also respects revoked sessions.
  Successful lookups are cached for ``AUTH_CACHE_TTL_S`` (5 min), keyed by a SHA-256 of the token
  (the raw token is never stored), and never beyond the token's own ``exp``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .errors import AppError

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")
DEV_NAMESPACE = uuid.UUID("6f1c2a52-7d0e-4f6b-9a0d-1f0e4d2b9c11")


@dataclass(frozen=True)
class User:
    id: str
    email: str | None


def _unauthorized(msg: str = "Missing or invalid access token") -> AppError:
    return AppError(401, "unauthorized", msg, headers={"WWW-Authenticate": "Bearer"})


def _jwt_exp(token: str) -> float | None:
    """Read ``exp`` without verifying (only used to bound the cache lifetime)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:  # noqa: BLE001
        return None


class Authenticator:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None, clock=time.time):
        self.settings = settings
        self.http = http
        self.clock = clock
        self._cache: dict[str, tuple[User, float]] = {}
        self._lock = threading.Lock()

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    def dev_user(self, email: str) -> User:
        return User(id=str(uuid.uuid5(DEV_NAMESPACE, email.lower())), email=email.lower())

    async def authenticate(self, authorization: str | None) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise _unauthorized()
        token = authorization[7:].strip()
        if not token or len(token) > 8192:
            raise _unauthorized()
        if token.startswith("dev:"):
            if self.settings.AUTH_MODE != "dev":
                raise _unauthorized("Dev tokens are disabled on this server")
            email = token[4:]
            if not _EMAIL_RE.match(email):
                raise _unauthorized("Dev token must look like dev:<email>")
            return self.dev_user(email)
        if self.settings.AUTH_MODE != "supabase":
            raise _unauthorized("Use a dev token (Bearer dev:<email>) in development")
        return await self._supabase_user(token)

    async def _supabase_user(self, token: str) -> User:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[1] > now:
                return hit[0]
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=10)
        try:
            resp = await self.http.get(
                f"{self.settings.SUPABASE_URL}/auth/v1/user",
                headers={"apikey": self.settings.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as e:
            raise AppError(503, "auth_unavailable", "Could not reach the authentication service") from e
        if resp.status_code in (401, 403):
            raise _unauthorized("Session expired or invalid - please log in again")
        if resp.status_code != 200:
            raise AppError(503, "auth_unavailable", f"Authentication service returned {resp.status_code}")
        data = resp.json()
        if not data.get("id"):
            raise _unauthorized()
        user = User(id=str(data["id"]), email=data.get("email"))
        expires = now + self.settings.AUTH_CACHE_TTL_S
        exp = _jwt_exp(token)
        if exp is not None:
            expires = min(expires, exp)
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            self._cache[key] = (user, expires)
        return user


# declared so Swagger UI (/docs) shows an "Authorize" button; parsing is done below
_bearer = HTTPBearer(auto_error=False, description="Supabase access token, or dev:<email> when AUTH_MODE=dev")


async def current_user(request: Request, _: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    """FastAPI dependency used by every route except /health."""
    auth: Authenticator = request.app.state.auth
    user = await auth.authenticate(request.headers.get("authorization"))
    request.state.user_id = user.id
    return user
