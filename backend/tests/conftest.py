"""Shared test fixtures.

DB tests need Postgres + pgvector at TEST_DATABASE_URL
(default postgresql://postgres:postgres@localhost:5432/verihire_test).
``python scripts/test_backend.py`` starts one for you. The database name MUST contain "test":
the schema is wiped at the start of the session.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for p in (str(BACKEND), str(ROOT / "ml"), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.config import Settings  # noqa: E402
from app.eventloop import selector_loop  # noqa: E402
from app.main import create_app  # noqa: E402
from devtools import synthpdf  # noqa: E402

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/verihire_test")
TABLES = ["audit_log", "feedback", "analyses", "search_runs", "job_descriptions", "chunks", "candidate_pii",
          "candidates", "job_documents", "documents", "ingestion_jobs"]


def _db_reachable(url: str) -> str | None:
    try:
        with psycopg.connect(url, connect_timeout=3):
            return None
    except Exception as e:  # noqa: BLE001
        return str(e).strip().splitlines()[0]


@pytest.fixture(scope="session")
def db_url() -> str:
    err = _db_reachable(TEST_DB_URL)
    if err:
        msg = (f"Postgres not reachable at {TEST_DB_URL} ({err}). Run `python scripts/test_backend.py` "
               "(starts a throw-away Postgres) or set TEST_DATABASE_URL.")
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    dbname = psycopg.conninfo.conninfo_to_dict(TEST_DB_URL).get("dbname", "")
    if "test" not in dbname:
        pytest.fail(f"refusing to wipe database {dbname!r}: TEST_DATABASE_URL must point at a *test* database")
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:  # wipe only VeriHire's own objects
        conn.execute("drop table if exists " + ", ".join(TABLES + ["schema_migrations"]) + " cascade")
        conn.execute("drop function if exists hybrid_search_chunks(text, text, vector, integer, real, integer, text[], text, integer)")
    import migrate  # scripts/migrate.py

    try:
        migrate.migrate(TEST_DB_URL, local_shim=True, verbose=False)
    except psycopg.errors.InsufficientPrivilege:  # hosted Postgres may forbid CREATE ROLE: run without the shim
        migrate.migrate(TEST_DB_URL, local_shim=False, verbose=False)
    return TEST_DB_URL


@pytest.fixture
def clean_db(db_url: str) -> str:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("truncate " + ", ".join(TABLES) + " restart identity cascade")
    return db_url


@pytest.fixture
def settings_factory(clean_db: str, tmp_path: Path) -> Callable[..., Settings]:
    def make(**overrides) -> Settings:
        base = dict(
            APP_ENV="test", AUTH_MODE="dev", STORAGE_MODE="local", ML_MODE="stub", DATABASE_URL=clean_db,
            LOCAL_STORAGE_DIR=str(tmp_path / "storage"), INGEST_WORKERS_ENABLED=False, LOG_JSON=False,
            LOG_LEVEL="WARNING", FRONTEND_DIR=str(tmp_path / "no-frontend"), APP_SECRET="test-secret-" + "x" * 32,
            DB_POOL_MAX=10,
        )
        base.update(overrides)
        return Settings(_env_file=None, **base)

    return make


@pytest.fixture
def client_factory(settings_factory) -> Iterator[Callable[..., TestClient]]:
    opened: list[TestClient] = []

    def make(settings: Settings | None = None, **kwargs) -> TestClient:
        app = create_app(settings or settings_factory(), **kwargs)
        # selector loop: psycopg async cannot run on Windows' default ProactorEventLoop
        c = TestClient(app, raise_server_exceptions=False, backend_options={"loop_factory": selector_loop})
        c.__enter__()
        opened.append(c)
        return c

    yield make
    for c in reversed(opened):
        c.__exit__(None, None, None)


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory()


# ------------------------------------------------------------------ helpers
def auth(email: str = "alice@example.com") -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{email}"}


A = auth("alice@example.com")
B = auth("bob@example.com")


def upload(c: TestClient, pdfs: list[tuple[str, bytes]], headers=A, field: str = "files"):
    return c.post("/api/v1/documents/upload", headers=headers,
                  files=[(field, (name, data, "application/pdf")) for name, data in pdfs])


def drain(c: TestClient, concurrency: int = 1) -> int:
    return c.portal.call(c.app.state.ingestion.drain, concurrency)


@contextmanager
def db_conn(url: str):
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def seed(c: TestClient, n_random: int = 8, headers=A) -> dict:
    """Upload n random resumes + one ideal backend candidate; ingest; create the backend JD."""
    pdfs = [(f"r{i}.pdf", synthpdf.make_resume(100 + i).pdf) for i in range(n_random)]
    best = synthpdf.make_resume(999, role="backend", skills=["Python", "FastAPI", "Docker", "PostgreSQL"], years=6)
    pdfs.append(("best.pdf", best.pdf))
    r = upload(c, pdfs, headers=headers)
    assert r.status_code == 202, r.text
    drain(c)
    jd = c.post("/api/v1/job-descriptions", json={"text": synthpdf.JD_BACKEND}, headers=headers)
    assert jd.status_code == 201, jd.text
    best_doc = r.json()["document_ids"][-1]
    return {"upload": r.json(), "jd": jd.json(), "best": best, "best_doc": best_doc}


def assert_ideal_near_top(results: list[dict], best_doc: str) -> None:
    """The planted ideal candidate must be in the top 2, and everyone ranked above it must also meet every required
    skill. Not "exactly #1": the random resumes can contain another fully qualified candidate (seed 104: backend,
    6 years, FastAPI => Python, PostgreSQL), and which of two equally qualified people wins depends on the embedder
    (stand-in vs real BGE) - both orders are correct."""
    ranks = [r["document_id"] for r in results]
    assert best_doc in ranks[:2], [(r["rank"], r["missing_required"]) for r in results[:3]]
    for r in results[: ranks.index(best_doc)]:
        assert r["missing_required"] == [], r
