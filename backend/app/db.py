"""Async Postgres access (psycopg 3 + psycopg_pool).

* prepared statements disabled (``prepare_threshold=None``) so it works behind the Supabase
  *transaction* pooler (PgBouncer cannot track per-session prepared statements);
* rows come back as dicts; embeddings are sent as text literals cast with ``::vector``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("verihire.db")


def vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.url = url
        self.pool = AsyncConnectionPool(
            url, min_size=min_size, max_size=max_size, open=False, timeout=15,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": True},
            check=AsyncConnectionPool.check_connection,
        )

    async def open(self) -> None:
        from .eventloop import is_proactor

        if is_proactor(asyncio.get_running_loop()):  # fail in 1 s with a clear message, not after a 30 s pool timeout
            raise RuntimeError(
                "Windows ProactorEventLoop detected: psycopg async needs a selector loop. Start the server with "
                "`uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop` "
                "(tests: TestClient(..., backend_options={'loop_factory': selector_loop})).")
        await self.pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self.pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Everything inside commits together or not at all."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                yield conn

    async def fetch_one(self, sql: str, params: Sequence[Any] | dict | None = None) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def fetch_all(self, sql: str, params: Sequence[Any] | dict | None = None) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def execute(self, sql: str, params: Sequence[Any] | dict | None = None) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def ping(self) -> bool:
        try:
            row = await self.fetch_one("select 1 as ok")
            return bool(row and row["ok"] == 1)
        except Exception:  # noqa: BLE001 - health must never raise
            log.warning("db ping failed", exc_info=True)
            return False
