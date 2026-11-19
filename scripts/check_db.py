#!/usr/bin/env python3
"""Diagnose why the tests cannot get a Postgres + pgvector database, and say exactly what to do.

    python scripts/check_db.py            # read-only checks
    python scripts/check_db.py --start    # also try to start the throw-away Docker database (then removes it)

Paste the whole output if you need help.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend as tb  # noqa: E402

OK, BAD, INFO = "[ OK ]", "[FAIL]", "[INFO]"


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", action="store_true", help="try starting the throw-away Docker database")
    a = ap.parse_args()
    fixes: list[str] = []
    print(f"{INFO} OS: {platform.system()} {platform.release()} | Python {sys.version.split()[0]} | {sys.executable}")
    shell = os.environ.get("PSModulePath") and "PowerShell" or os.environ.get("ComSpec", os.environ.get("SHELL", "?"))
    print(f"{INFO} shell: {shell}")

    try:
        import psycopg
        print(f"{OK} psycopg {psycopg.__version__} installed")
    except ImportError:
        print(f"{BAD} psycopg not installed")
        fixes.append("pip install -r backend/requirements-dev.txt")

    ok, why = tb.docker_status()
    print(f"{OK if ok else BAD} {why}")
    if not ok:
        fixes.append("Install/start Docker Desktop, wait for 'Engine running', then rerun (or use a hosted database, see README)")
    elif shutil.which("docker"):
        imgs = subprocess.run(["docker", "images", "-q", "pgvector/pgvector:pg16"], capture_output=True, text=True).stdout.strip()
        print(f"{INFO} image pgvector/pgvector:pg16 {'already downloaded' if imgs else 'not downloaded yet (first run pulls ~150 MB)'}")
        ps = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Ports}}"], capture_output=True, text=True).stdout
        for line in ps.strip().splitlines():
            print(f"{INFO} running container: {line}")

    print(f"{INFO} port 5432: {'IN USE (some Postgres is listening)' if port_open(5432) else 'free (no Postgres on the default port)'}")

    env_url = os.environ.get("TEST_DATABASE_URL")
    print(f"{INFO} TEST_DATABASE_URL: {tb._mask(env_url) if env_url else 'NOT SET in this terminal'}")
    urls = ([("TEST_DATABASE_URL", env_url)] if env_url else []) + [
        ("default", "postgresql://postgres:postgres@localhost:5432/verihire_test")]
    usable = None
    for label, url in urls:
        ok, why = tb._ensure_database(url)
        print(f"{OK if ok else BAD} {label}: {why}")
        if ok:
            usable = url
            break
        if "password" in why:
            fixes.append("Your Postgres has a different password: set TEST_DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/verihire_test")
        if "NO pgvector" in why:
            fixes.append("Your Postgres lacks pgvector: stop it (or use another port) and use Docker's pgvector/pgvector:pg16 image")

    if usable is None and a.start:
        tmp = tb.TempPostgres()
        url = tmp.start_docker()
        print(f"{OK if url else BAD} throw-away Docker database: {url or tmp.reason}")
        tmp.stop()
        if url:
            usable = url

    print()
    if usable:
        print(f"RESULT: a usable database exists -> {tb._mask(usable)}")
        if env_url and usable != env_url:
            print("        (but not the one in TEST_DATABASE_URL)")
        print("        Run:  python scripts/test_all.py")
        return 0
    print("RESULT: no usable database yet. Do this:")
    for i, f in enumerate(dict.fromkeys(fixes) or ["Start Docker Desktop, then: python scripts/test_all.py"], 1):
        print(f"  {i}. {f}")
    if os.name == "nt":
        print("  Note (Windows): in PowerShell set variables with  $env:TEST_DATABASE_URL=\"...\"  (plain `set` does nothing there).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
