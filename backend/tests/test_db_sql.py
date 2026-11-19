"""Migrations and the hybrid_search_chunks SQL function, tested directly against Postgres."""

from __future__ import annotations

import shutil

import psycopg
import pytest

import migrate
from app.db import vector_literal
from conftest import A, db_conn, seed
from docint.stubs import StubEmbedder


def test_migrations_are_idempotent(db_url):
    assert migrate.migrate(db_url, verbose=False) == []
    assert {n: st for n, st in migrate.status(db_url)} == {
        "001_init.sql": "applied", "002_hybrid_fn.sql": "applied", "003_lockdown.sql": "applied"}


def test_edited_migration_is_refused_but_rerunnable_one_reapplies(db_url, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(migrate.MIGRATIONS_DIR, d)
    (d / "001_init.sql").write_text((d / "001_init.sql").read_text() + "\n-- edited\n")
    with pytest.raises(migrate.MigrationError, match="001_init.sql"):
        migrate.migrate(db_url, directory=d, verbose=False)
    shutil.copy(migrate.MIGRATIONS_DIR / "001_init.sql", d / "001_init.sql")
    (d / "003_lockdown.sql").write_text((d / "003_lockdown.sql").read_text() + "\n-- tweak\n")
    assert migrate.migrate(db_url, directory=d, verbose=False) == ["003_lockdown.sql"]
    assert migrate.migrate(db_url, verbose=False) == ["003_lockdown.sql"]  # back to the original content


def test_rls_enabled_and_anon_has_no_access(db_url):
    with db_conn(db_url) as conn:
        if not conn.execute("select 1 from pg_roles where rolname = 'anon'").fetchone():
            pytest.skip("no 'anon' role on this server (hosted DB without the local shim)")
        rows = conn.execute("select relname, relrowsecurity from pg_class where relname = any(%s)",
                            (["documents", "chunks", "candidates", "candidate_pii", "audit_log"],)).fetchall()
        assert len(rows) == 5 and all(r[1] for r in rows)
        # Supabase grants anon broad default privileges; 003_lockdown must take them away again
        conn.execute("grant usage on schema public to anon")
        conn.execute("grant select on candidate_pii to anon")
        conn.execute((migrate.MIGRATIONS_DIR / "003_lockdown.sql").read_text())
        conn.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from candidate_pii")
        conn.execute("reset role")


def _search(conn, owner, ts, text, **kw):
    q = vector_literal(StubEmbedder().embed_query(text))
    args = {"min_years": None, "min_edu": None, "skills": None, **kw}
    return conn.execute(
        "select * from hybrid_search_chunks(%s, %s, %s::vector, 200, %s::real, %s::int, %s::text[], 'resume', 60)",
        (owner, ts, q, args["min_years"], args["min_edu"], args["skills"])).fetchall()


def test_hybrid_function_rrf_math_and_filters(client, clean_db):
    seed(client)
    owner = client.get("/api/v1/me", headers=A).json()["id"]
    with db_conn(clean_db) as conn:
        rows = _search(conn, owner, "python | fastapi", "python fastapi postgresql")
        assert rows
        cols = [d.name for d in conn.execute("select * from hybrid_search_chunks('x','',null) limit 0").description]
        for r in rows:
            r = dict(zip(cols, r, strict=True))
            expected = (1 / (60 + r["vec_rank"]) if r["vec_rank"] else 0) + (1 / (60 + r["fts_rank"]) if r["fts_rank"] else 0)
            assert abs(r["rrf_score"] - expected) < 1e-9
        scores = [dict(zip(cols, r, strict=True))["rrf_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)
        named = [dict(zip(cols, r, strict=True)) for r in rows]
        assert any(r["fts_rank"] for r in named) and any(r["vec_rank"] for r in named)
        # empty ts_query -> vector only; null embedding -> full-text only
        only_vec = [dict(zip(cols, r, strict=True)) for r in _search(conn, owner, "", "python")]
        assert only_vec and all(r["fts_rank"] is None for r in only_vec)
        only_fts = conn.execute("select fts_rank, vec_rank from hybrid_search_chunks(%s, 'python', null)", (owner,)).fetchall()
        assert only_fts and all(v is None for _, v in only_fts)
        # other owner sees nothing
        assert _search(conn, "someone-else", "python", "python") == []
        # filters
        assert _search(conn, owner, "python", "python", skills=["cobol"]) == []
        assert _search(conn, owner, "python", "python", min_years=99) == []
        assert _search(conn, owner, "python", "python", skills=[]) != []


def test_explain_runs_and_is_fast(client, clean_db):
    seed(client, n_random=20)
    owner = client.get("/api/v1/me", headers=A).json()["id"]
    q = vector_literal(StubEmbedder().embed_query("python"))
    with db_conn(clean_db) as conn:
        plan = conn.execute("explain (analyze, format json) select * from hybrid_search_chunks(%s, 'python | sql', %s::vector)",
                            (owner, q)).fetchone()[0][0]
    assert plan["Execution Time"] < 500
