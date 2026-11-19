"""Event-loop factory that works with psycopg's async mode on EVERY OS.

On Windows, Python's default asyncio loop is ``ProactorEventLoop``, which psycopg cannot use in async mode
("Psycopg cannot use the 'ProactorEventLoop'..."), and uvicorn explicitly picks Proactor on Windows. This factory returns
a ``SelectorEventLoop`` instead. Use it everywhere an event loop is created:

    uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop      (server; any OS)
    TestClient(app, backend_options={"loop_factory": selector_loop})               (tests)
"""

from __future__ import annotations

import asyncio
import selectors
import sys


def selector_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()  # already a selector loop on Linux/macOS


def is_proactor(loop: asyncio.AbstractEventLoop) -> bool:
    return type(loop).__name__ in ("ProactorEventLoop", "_WindowsProactorEventLoop")
