#!/usr/bin/env python3
"""Idempotent SQL migration runner for VeriHire (owner: Raj).

    python scripts/migrate.py                         # apply pending migrations (DATABASE_URL)
    python scripts/migrate.py --local-shim            # + create Supabase-like roles locally first
    python scripts/migrate.py --status                # show applied / pending / changed
    python scripts/migrate.py --supabase-storage      # + create the private 'resumes' bucket (Supabase only)

How it works
  * Files in db/migrations/*.sql run in name order, each in its own transaction.
  * Applied files are recorded in ``schema_migrations`` with a SHA-256 checksum.
  * A file whose first line is ``-- migrate: rerunnable`` is re-applied when its checksum changes
    (functions, grants). Any other already-applied file that changed is an ERROR — write a new file.
  * A Postgres advisory lock makes concurrent runs (two containers booting) safe.
  * Prepared statements are disabled, so it also works through the Supabase transaction pooler.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "db" / "migrations"
SHIM = ROOT / "db" / "local" / "auth_shim.sql"
STORAGE_SQL = ROOT / "db" / "supabase" / "storage_policies.sql"
LOCK_KEY = 74_201_119  # arbitrary constant for pg_advisory_lock
RERUNNABLE_MARK = "-- migrate: rerunnable"


class MigrationError(RuntimeError):
    pass


@dataclass
class Migration:
    name: str
    sql: str
    checksum: str
    rerunnable: bool


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        out.append(Migration(
            name=path.name, sql=sql, checksum=hashlib.sha256(sql.encode()).hexdigest(),
            rerunnable=sql.lstrip().lower().startswith(RERUNNABLE_MARK),
        ))
    return out


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=10)


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        "create table if not exists schema_migrations ("
        " name text primary key, checksum text not null, applied_at timestamptz not null default now())"
    )


def status(url: str, directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    with _connect(url) as conn:
        _ensure_table(conn)
        applied = dict(conn.execute("select name, checksum from schema_migrations").fetchall())
    rows = []
    for m in load_migrations(directory):
        if m.name not in applied:
            rows.append((m.name, "pending"))
        elif applied[m.name] != m.checksum:
            rows.append((m.name, "changed (will re-run)" if m.rerunnable else "CHANGED - not allowed"))
        else:
            rows.append((m.name, "applied"))
    return rows


def migrate(url: str, *, local_shim: bool = False, supabase_storage: bool = False,
            directory: Path = MIGRATIONS_DIR, verbose: bool = True) -> list[str]:
    """Apply pending migrations. Returns the names that were (re-)applied."""
    log = print if verbose else (lambda *a, **k: None)
    done: list[str] = []
    with _connect(url) as conn:
        conn.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            if local_shim:
                conn.execute(SHIM.read_text(encoding="utf-8"))
                log("applied db/local/auth_shim.sql")
            _ensure_table(conn)
            applied = dict(conn.execute("select name, checksum from schema_migrations").fetchall())
            for m in load_migrations(directory):
                prev = applied.get(m.name)
                if prev == m.checksum:
                    continue
                if prev is not None and not m.rerunnable:
                    raise MigrationError(
                        f"{m.name} was already applied but its content changed. "
                        "Never edit an applied migration - add a new numbered file instead.")
                with conn.transaction():
                    conn.execute(m.sql)
                    conn.execute(
                        "insert into schema_migrations (name, checksum) values (%s, %s) "
                        "on conflict (name) do update set checksum = excluded.checksum, applied_at = now()",
                        (m.name, m.checksum))
                done.append(m.name)
                log(f"{'re-applied' if prev else 'applied'} {m.name}")
            if supabase_storage:
                conn.execute(STORAGE_SQL.read_text(encoding="utf-8"))
                log("applied db/supabase/storage_policies.sql")
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
    if not done:
        log("database is up to date")
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--local-shim", action="store_true", help="create anon/authenticated roles (local only)")
    p.add_argument("--supabase-storage", action="store_true", help="create the private resumes bucket")
    p.add_argument("--status", action="store_true", help="show migration status and exit")
    a = p.parse_args(argv)
    if not a.database_url:
        p.error("set DATABASE_URL or pass --database-url")
    try:
        if a.status:
            for name, st in status(a.database_url):
                print(f"{name:<28} {st}")
            return 0
        migrate(a.database_url, local_shim=a.local_shim, supabase_storage=a.supabase_storage)
        return 0
    except (MigrationError, psycopg.Error) as e:
        print(f"migration failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
