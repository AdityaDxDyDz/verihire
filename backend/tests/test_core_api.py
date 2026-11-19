"""Health, auth, error envelope, request id, routing."""

from __future__ import annotations

import time

import httpx

from app.auth import Authenticator
from conftest import A, auth


def test_health_no_auth(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["contract_version"] == "1.2.0"
    assert body["ml_mode"] == "stub" and body["stubs_in_use"] is True
    assert set(body["ml_components"]) == {"ingest", "embedder", "llm", "jd_parser", "skillgap", "queries", "reranker"}
    assert body["auth_mode"] == "dev" and body["storage_mode"] == "local"


def test_me_requires_token(client):
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "},
                    {"Authorization": "Bearer dev:not-an-email"}, {"Authorization": "Bearer some.jwt.token"}):
        r = client.get("/api/v1/me", headers=headers)
        assert r.status_code == 401, headers
        assert r.json()["error"]["code"] == "unauthorized"
        assert r.headers["www-authenticate"] == "Bearer"


def test_me_dev_token_is_stable(client):
    r1 = client.get("/api/v1/me", headers=auth("Raj@Example.com")).json()
    r2 = client.get("/api/v1/me", headers=auth("raj@example.com")).json()
    assert r1 == r2 and r1["email"] == "raj@example.com"
    assert client.get("/api/v1/me", headers=auth("other@example.com")).json()["id"] != r1["id"]


def test_error_envelope_unknown_route_and_validation(client):
    r = client.get("/api/v1/nope", headers=A)
    assert r.status_code == 404 and r.json() == {"error": {"code": "not_found", "message": "No API route /nope"}}
    r = client.post("/api/v1/job-descriptions", headers=A, json={"text": "too short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error" and "text" in r.json()["error"]["message"]
    r = client.post("/api/v1/job-descriptions", headers=A, content=b"{bad json", )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_request_id_and_security_headers(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    r = client.get("/api/v1/health", headers={"X-Request-ID": "bad id with spaces\nx"})
    assert r.headers["x-request-id"] != "bad id with spaces\nx" and len(r.headers["x-request-id"]) == 16


def test_unhandled_error_is_enveloped(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("secret internals")
    monkeypatch.setattr("app.services.jobdesc.list_all", boom)
    r = client.get("/api/v1/job-descriptions", headers=A)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong. Please retry."}}


def test_openapi_and_docs(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in ["/api/v1/health", "/api/v1/me", "/api/v1/documents/upload", "/api/v1/documents/retry",
              "/api/v1/jobs/{job_id}", "/api/v1/documents", "/api/v1/documents/{document_id}/pdf-url",
              "/api/v1/documents/{document_id}", "/api/v1/job-descriptions", "/api/v1/job-descriptions/{jd_id}",
              "/api/v1/search", "/api/v1/search/{search_id}", "/api/v1/search/{search_id}/export.csv",
              "/api/v1/analysis", "/api/v1/candidates/{candidate_id}", "/api/v1/feedback"]:
        assert p in paths, p
    assert client.get("/docs").status_code == 200


def test_root_without_frontend(client):
    assert client.get("/").json()["docs"] == "/docs"


def test_frontend_is_served_when_present(client_factory, settings_factory, tmp_path):
    fe = tmp_path / "fe"
    fe.mkdir()
    (fe / "index.html").write_text("<h1>VeriHire</h1>")
    c = client_factory(settings_factory(FRONTEND_DIR=str(fe)))
    assert "VeriHire" in c.get("/").text
    assert c.get("/api/v1/health").status_code == 200
    r = c.get("/api/v1/does-not-exist", headers=A)  # API 404 stays JSON even with the static mount
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------- Supabase auth (mocked HTTP)
def _supabase_client(client_factory, settings_factory, handler, clock=time.time):
    s = settings_factory(AUTH_MODE="supabase", SUPABASE_URL="https://proj.supabase.co", SUPABASE_ANON_KEY="anon-key")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client_factory(s, authenticator=Authenticator(s, http=http, clock=clock))


def test_supabase_token_verified_and_cached(client_factory, settings_factory):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        assert req.url == "https://proj.supabase.co/auth/v1/user"
        assert req.headers["apikey"] == "anon-key"
        if req.headers["authorization"] == "Bearer good-token":
            return httpx.Response(200, json={"id": "11111111-1111-1111-1111-111111111111", "email": "hr@corp.com"})
        return httpx.Response(401, json={"msg": "invalid"})

    c = _supabase_client(client_factory, settings_factory, handler)
    for _ in range(3):
        r = c.get("/api/v1/me", headers={"Authorization": "Bearer good-token"})
        assert r.json() == {"id": "11111111-1111-1111-1111-111111111111", "email": "hr@corp.com"}
    assert len(calls) == 1  # cached
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert c.get("/api/v1/me", headers=A).status_code == 401  # dev tokens refused in supabase mode


def test_supabase_cache_expires(client_factory, settings_factory):
    now = [1000.0]
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"id": "u1", "email": "a@b.co"})

    c = _supabase_client(client_factory, settings_factory, handler, clock=lambda: now[0])
    c.get("/api/v1/me", headers={"Authorization": "Bearer t"})
    now[0] += 301
    c.get("/api/v1/me", headers={"Authorization": "Bearer t"})
    assert len(calls) == 2


def test_supabase_down_is_503(client_factory, settings_factory):
    def handler(req):
        raise httpx.ConnectError("down")

    c = _supabase_client(client_factory, settings_factory, handler)
    r = c.get("/api/v1/me", headers={"Authorization": "Bearer t"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "auth_unavailable"


def test_auto_migrate_on_startup(client_factory, settings_factory):
    c = client_factory(settings_factory(AUTO_MIGRATE=True))
    assert c.get("/api/v1/health").json()["db"] == "ok"
    from app.main import _run_migrations
    assert _run_migrations(c.app.state.settings.DATABASE_URL) == []  # already up to date, safe to call again


def test_startup_refuses_production_with_dev_auth(settings_factory):
    import pytest

    from app.config import ConfigError
    from app.main import create_app
    with pytest.raises(ConfigError):
        create_app(settings_factory(APP_ENV="production"))


def test_windows_proactor_loop_fails_fast_with_a_clear_message(monkeypatch):
    """psycopg async cannot use Windows' ProactorEventLoop: the app must say so at once, not time out."""
    import asyncio

    import pytest

    from app import eventloop
    from app.db import Database

    real_is_proactor = eventloop.is_proactor
    monkeypatch.setattr(eventloop, "is_proactor", lambda loop: True)
    with pytest.raises(RuntimeError, match="selector_loop"):
        asyncio.run(Database("postgresql://nobody@127.0.0.1:1/x").open())
    loop = eventloop.selector_loop()
    assert isinstance(loop, asyncio.SelectorEventLoop) and not real_is_proactor(loop)
    loop.close()


def test_config_js_is_generated_from_settings(client_factory, settings_factory, client):
    r = client.get("/config.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert '"mode": "dev"' in r.text and "service" not in r.text.lower()
    s = settings_factory(AUTH_MODE="supabase", SUPABASE_URL="https://p.supabase.co", SUPABASE_ANON_KEY="anon-pub",
                         SUPABASE_SERVICE_KEY="SECRET-SERVICE-KEY")
    body = client_factory(s).get("/config.js").text
    assert '"mode": "supabase"' in body and "anon-pub" in body and "SECRET-SERVICE-KEY" not in body
