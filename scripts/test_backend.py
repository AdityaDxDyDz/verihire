#!/usr/bin/env python3
"""ONE command to test Aditya Raj's whole backend on its own (no frontend, no Supabase, no LLM key).

    python scripts/test_backend.py                 # everything
    python scripts/test_backend.py --install       # pip-install missing dependencies first
    python scripts/test_backend.py -k search       # only tests whose name matches "search"
    python scripts/test_backend.py --skip-smoke    # skip the live-server smoke test
    python scripts/test_backend.py --write-examples  # also refresh docs/contracts/examples_backend/*.json
    python scripts/test_backend.py --track-b ../verihire-harsh-track-b-handoff   # test against Harsh's code now
    python scripts/test_backend.py --real-model      # integration tests with the real BGE model (internet once)

Steps
  1. dependency check (Python >= 3.11, FastAPI, psycopg, ...)
  2. a Postgres + pgvector test database, found or started automatically, in this order:
       a) TEST_DATABASE_URL if set and reachable
       b) postgresql://postgres:postgres@localhost:5432/verihire_test (database created if missing)
       c) a throw-away Docker container  pgvector/pgvector:pg16  on port 54329
       d) a throw-away local cluster (initdb) on port 54329 if Postgres + pgvector are installed
  3. lint (ruff) on backend/ and scripts/
  4. pytest: unit + API + DB + ingestion + search + analysis + isolation tests (with coverage)
  5. live smoke test: starts uvicorn on a free port and runs scripts/smoke_test.py against it
Exit code 0 only if every step passed.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
REQUIRED = {  # import name -> pip name
    "fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "psycopg": "psycopg[binary]", "psycopg_pool": "psycopg-pool",
    "pydantic": "pydantic", "pydantic_settings": "pydantic-settings", "multipart": "python-multipart",
    "httpx": "httpx", "pytest": "pytest",
}
OPTIONAL = {"pytest_cov": "pytest-cov", "ruff": "ruff"}

GREEN, RED, YELLOW, BOLD, END = ("\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m") if sys.stdout.isatty() else ("",) * 5
results: list[tuple[str, str, str]] = []


def step(name: str, status: str, detail: str = "") -> None:
    colour = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW}[status]
    results.append((name, status, detail))
    print(f"{colour}{BOLD}[{status}]{END} {name}{(' - ' + detail) if detail else ''}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ 1. dependencies
def check_deps(install: bool) -> bool:
    if sys.version_info < (3, 11):  # noqa: UP036 - friendly message on old Pythons
        step("python version", "FAIL", f"need >= 3.11, have {sys.version.split()[0]}")
        return False
    missing = []
    for mod, pip_name in {**REQUIRED, **OPTIONAL}.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pip_name)
    if missing and install:
        print(f"installing: {' '.join(missing)}")
        cmd = [sys.executable, "-m", "pip", "install", "-q", *missing]
        if subprocess.run(cmd).returncode != 0:
            subprocess.run(cmd + ["--break-system-packages"])
        importlib.invalidate_caches()
        return check_deps(install=False)
    hard = [m for m in missing if m not in OPTIONAL.values()]
    if hard:
        step("dependencies", "FAIL", "missing " + ", ".join(hard) +
             "  ->  pip install -r backend/requirements-dev.txt   (or rerun with --install)")
        return False
    step("dependencies", "PASS", f"python {sys.version.split()[0]}" + (f"; optional missing: {', '.join(missing)}" if missing else ""))
    return True


# ------------------------------------------------------------------ 2. database
def probe(url: str) -> tuple[bool, str]:
    """Try a database URL. Returns (usable, human-readable reason)."""
    try:
        import psycopg
        from psycopg import conninfo
    except ImportError:
        return False, "psycopg is not installed (pip install -r backend/requirements-dev.txt)"
    try:
        info = conninfo.conninfo_to_dict(url)
    except Exception as e:  # noqa: BLE001
        return False, f"invalid URL ({e})"
    where = f"{info.get('host', 'localhost')}:{info.get('port', 5432)}"
    try:
        with psycopg.connect(url, connect_timeout=4) as c:
            if c.execute("select 1 from pg_available_extensions where name='vector'").fetchone() is None:
                return False, (f"Postgres at {where} works but has NO pgvector extension "
                               "(use the Docker image pgvector/pgvector:pg16, or install pgvector for your Postgres)")
            return True, f"ok ({where})"
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        low = msg.lower()
        if "password authentication failed" in low or "authentication failed" in low:
            return False, f"Postgres at {where} rejected the user/password in the URL - put YOUR password in TEST_DATABASE_URL"
        if "does not exist" in low and "database" in low:
            return False, f"Postgres at {where} is running but database {info.get('dbname')!r} does not exist (could not create it)"
        if "refused" in low or "could not connect" in low or "connection failed" in low or "timeout" in low:
            return False, f"nothing is listening on {where} (no database server running there)"
        return False, f"{where}: {msg}"


def _reachable(url: str) -> bool:
    return probe(url)[0]


def _has_pgvector(url: str) -> bool:
    return probe(url)[0]


def _ensure_database(url: str) -> tuple[bool, str]:
    """If the server is reachable but the database is missing, create it. Returns (ok, reason)."""
    ok, reason = probe(url)
    if ok or "does not exist" not in reason:
        return ok, reason
    import psycopg
    from psycopg import conninfo
    info = conninfo.conninfo_to_dict(url)
    for admin_db in ("postgres", "template1"):
        try:
            with psycopg.connect(conninfo.make_conninfo(url, dbname=admin_db), autocommit=True, connect_timeout=4) as c:
                c.execute(f'create database "{info["dbname"]}"')
            return probe(url)
        except Exception:  # noqa: BLE001
            continue
    return False, reason


def docker_status() -> tuple[bool, str]:
    exe = shutil.which("docker")
    if not exe:
        return False, "Docker is not installed (or not on PATH) - install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    r = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
    if r.returncode != 0:
        first = (r.stderr or r.stdout).strip().splitlines()[:1]
        return False, ("Docker is installed but NOT RUNNING - open Docker Desktop and wait for 'Engine running'"
                       + (f" ({first[0][:160]})" if first else ""))
    return True, f"Docker {r.stdout.strip()} running"


class TempPostgres:
    def __init__(self) -> None:
        self.kind = ""
        self.container = "verihire-test-pg"
        self.datadir: Path | None = None
        self.bindir: Path | None = None
        self.prefix: list[str] = []
        self.reason = ""

    def start_docker(self) -> str | None:
        ok, why = docker_status()
        if not ok:
            self.reason = why
            return None
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
        print("pulling pgvector/pgvector:pg16 (first time ~150 MB, can take a few minutes)...", flush=True)
        pull = subprocess.run(["docker", "pull", "pgvector/pgvector:pg16"])
        if pull.returncode != 0:
            self.reason = "docker could not download pgvector/pgvector:pg16 (internet / proxy / Docker Hub login?)"
            return None
        port = free_port()
        r = subprocess.run(["docker", "run", "-d", "--rm", "--name", self.container, "-e", "POSTGRES_PASSWORD=postgres",
                            "-e", "POSTGRES_DB=verihire_test", "-p", f"127.0.0.1:{port}:5432", "pgvector/pgvector:pg16"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.reason = "docker run failed: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200]
            return None
        self.kind = "docker"
        url = f"postgresql://postgres:postgres@127.0.0.1:{port}/verihire_test"
        for _ in range(120):
            if probe(url)[0]:
                return url
            time.sleep(1)
        logs = subprocess.run(["docker", "logs", "--tail", "15", self.container], capture_output=True, text=True)
        self.reason = "container started but Postgres never became ready. Last log lines:\n" + (logs.stdout + logs.stderr)[-1500:]
        return None

    def _find_bindir(self) -> Path | None:
        if shutil.which("initdb"):
            return Path(shutil.which("initdb")).parent
        patterns = ["/usr/lib/postgresql/*/bin", "/usr/local/opt/postgresql*/bin", "/opt/homebrew/opt/postgresql*/bin",
                    "/Applications/Postgres.app/Contents/Versions/*/bin"]
        roots = [Path("/")]
        if os.name == "nt":
            patterns = ["Program Files/PostgreSQL/*/bin"]
            roots = [Path(os.environ.get("SystemDrive", "C:") + "/")]
        for root in roots:
            for pattern in patterns:
                for h in sorted(root.glob(pattern.lstrip("/")), reverse=True):
                    if (h / "initdb").exists() or (h / "initdb.exe").exists():
                        return h
        return None

    def start_local(self) -> str | None:
        self.bindir = self._find_bindir()
        if not self.bindir:
            self.reason = "no local Postgres installation found (initdb not on PATH)"
            return None
        self.datadir = Path(tempfile.mkdtemp(prefix="verihire-pg-"))
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # initdb refuses to run as root
            if not shutil.which("runuser"):
                self.reason = "running as root and runuser is unavailable"
                return None
            self.prefix = ["runuser", "-u", "postgres", "--"]
            shutil.chown(self.datadir, "postgres")
        data = self.datadir / "data"
        r = subprocess.run([*self.prefix, str(self.bindir / "initdb"), "-D", str(data), "-U", "postgres", "--auth=trust",
                            "-E", "UTF8"], capture_output=True, text=True)
        if r.returncode != 0:
            self.reason = "initdb failed: " + r.stderr.strip()[-300:]
            return None
        port = free_port()
        opts = f"-p {port} -c listen_addresses=127.0.0.1"
        if os.name != "nt":  # unix socket dir; skipped on Windows (paths with spaces break the -o string)
            opts += f' -k "{self.datadir}"'
        r = subprocess.run([*self.prefix, str(self.bindir / "pg_ctl"), "-D", str(data), "-l", str(self.datadir / "log"),
                            "-o", opts, "-w", "-t", "60", "start"], capture_output=True, text=True)
        if r.returncode != 0:
            log = (self.datadir / "log").read_text(errors="replace")[-600:] if (self.datadir / "log").exists() else ""
            self.reason = "pg_ctl start failed: " + (r.stderr.strip()[-300:] or log)
            return None
        self.kind = "local"
        url = f"postgresql://postgres@127.0.0.1:{port}/verihire_test"
        ok, why = _ensure_database(url)
        if not ok:
            self.reason = f"local Postgres from {self.bindir}: {why}"
            return None
        return url

    def stop(self) -> None:
        if self.kind == "docker":
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
        elif self.kind == "local" and self.bindir and self.datadir:
            subprocess.run([*self.prefix, str(self.bindir / "pg_ctl"), "-D", str(self.datadir / "data"), "-m", "fast", "stop"],
                           capture_output=True)
            shutil.rmtree(self.datadir, ignore_errors=True)
        self.kind = ""


def get_database(tmp: TempPostgres, fresh: bool) -> str | None:
    attempts: list[str] = []
    candidates = []
    if os.environ.get("TEST_DATABASE_URL") and not fresh:
        candidates.append(("TEST_DATABASE_URL", os.environ["TEST_DATABASE_URL"]))
    if not fresh:
        candidates.append(("default URL", "postgresql://postgres:postgres@localhost:5432/verihire_test"))
    for label, url in candidates:
        ok, why = _ensure_database(url)
        if ok:
            step("test database", "PASS", url)
            return url
        attempts.append(f"{label} {_mask(url)}: {why}")
    for starter, label in ((tmp.start_docker, "throw-away Docker database"), (tmp.start_local, "throw-away local database")):
        url = starter()
        if url:
            step("test database", "PASS", f"started {label} at {url}")
            return url
        attempts.append(f"{label}: {tmp.reason}")
        tmp.stop()
    step("test database", "FAIL", "no usable Postgres + pgvector. What was tried:\n      - " + "\n      - ".join(attempts)
         + "\n    Fix: see 'If the test database fails' in README.md, or run:  python scripts/check_db.py")
    return None


def _mask(url: str) -> str:
    import re
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", url)


# ------------------------------------------------------------------ 3-5
def run_lint() -> None:
    if not shutil.which("ruff") and importlib.util.find_spec("ruff") is None:
        step("lint (ruff)", "SKIP", "ruff not installed")
        return
    r = subprocess.run([sys.executable, "-m", "ruff", "check", "backend", "scripts", "--config", "backend/ruff.toml"], cwd=ROOT)
    step("lint (ruff)", "PASS" if r.returncode == 0 else "FAIL")


def run_pytest(db_url: str, k: str | None, write_examples: bool) -> bool:
    env = {**os.environ, "TEST_DATABASE_URL": db_url, "REQUIRE_DB": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if write_examples:
        env["WRITE_EXAMPLES"] = "1"
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if importlib.util.find_spec("pytest_cov"):
        cmd += ["--cov=app", "--cov-report=term-missing:skip-covered", "--cov-fail-under=70"]
    if k:
        cmd += ["-k", k]
    r = subprocess.run(cmd, cwd=BACKEND, env=env)
    step("pytest" + (f" -k {k}" if k else ""), "PASS" if r.returncode == 0 else "FAIL")
    return r.returncode == 0


def run_smoke(db_url: str) -> None:
    """Real server. With Track B present it runs ML_MODE=auto, i.e. Harsh's real PDF ingestion (and the real
    BGE model if it is cached / --real-model; otherwise the embedder falls back to the stub at once)."""
    port = free_port()
    ml_mode = "auto" if track_b_present() else "stub"
    store = tempfile.mkdtemp(prefix="verihire-store-")
    env = {**os.environ, "DATABASE_URL": db_url, "APP_ENV": "development", "AUTH_MODE": "dev", "STORAGE_MODE": "local",
           "ML_MODE": ml_mode, "LOCAL_STORAGE_DIR": store,
           "VERIHIRE_EMBED_OFFLINE": "0" if os.environ.get("VERIHIRE_REAL_MODEL") == "1" else "1",
           "AUTO_MIGRATE": "true", "INGEST_CONCURRENCY": "2",
           "INGEST_POLL_INTERVAL_S": "0.3", "LOG_LEVEL": "WARNING"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(BACKEND),
                            "--loop", "app.eventloop:selector_loop",  # psycopg async on Windows needs a selector loop
                            "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        import httpx
        for _ in range(240):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            step("live smoke test", "FAIL", "server did not start")
            return
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_test.py"), "--base-url",
                            f"http://127.0.0.1:{port}", "--timeout", "60"], cwd=ROOT)
        step(f"live smoke test (uvicorn ML_MODE={ml_mode} + smoke_test.py)", "PASS" if r.returncode == 0 else "FAIL")
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(store, ignore_errors=True)


TRACK_B_PARTS = ("ingest", "embed")  # Harsh's sub-packages; contracts.py / stubs.py stay the shared ones


def track_b_present(root: Path = ROOT) -> bool:
    return all((root / "ml" / "docint" / part / "__init__.py").is_file() for part in TRACK_B_PARTS)


def run_with_track_b(src: Path, passthrough: list[str], install: bool) -> int:
    """Copy this repo to a temp dir, lay Harsh's ml/docint/{ingest,embed} over it (= the merged repo), run there."""
    src = src.resolve()
    hits = [d for d in [src / "ml" / "docint", src / "docint", *src.glob("*/ml/docint")] if (d / "ingest").is_dir()]
    if not hits:
        step("track B code", "FAIL", f"no ml/docint/ingest found under {src}")
        return 1
    tb = hits[0]
    if install:
        for req in (tb.parent.parent / "requirements.txt", tb.parent.parent.parent / "requirements.txt"):
            if req.is_file():
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])
                break
    tmp = Path(tempfile.mkdtemp(prefix="verihire-merged-")) / "verihire"
    shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache",
                                                             ".data", ".coverage", ".cache", ".venv"))
    for part in TRACK_B_PARTS:
        shutil.rmtree(tmp / "ml" / "docint" / part, ignore_errors=True)
        shutil.copytree(tb / part, tmp / "ml" / "docint" / part, ignore=shutil.ignore_patterns("__pycache__"))
    if (tb.parent / "data_ingest").is_dir():
        shutil.copytree(tb.parent / "data_ingest", tmp / "ml" / "data_ingest", dirs_exist_ok=True)
    print(f"merged copy with Track B from {tb} -> {tmp}")
    try:
        return subprocess.run([sys.executable, str(tmp / "scripts" / "test_backend.py"), *passthrough]).returncode
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--install", action="store_true", help="pip install missing dependencies")
    p.add_argument("-k", help="only run tests matching this pytest expression")
    p.add_argument("--skip-lint", action="store_true")
    p.add_argument("--skip-smoke", action="store_true")
    p.add_argument("--fresh-db", action="store_true", help="always start a throw-away database")
    p.add_argument("--keep-db", action="store_true", help="leave the throw-away database running")
    p.add_argument("--write-examples", action="store_true", help="refresh docs/contracts/examples_backend/")
    p.add_argument("--track-b", metavar="DIR", help="folder with Harsh's handoff: test against it before merging")
    p.add_argument("--real-model", action="store_true", help="use the real BGE model in Track B integration tests")
    a = p.parse_args()
    if a.track_b:
        rest, skip = [], False
        for x in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if x == "--track-b":
                skip = True
                continue
            if not x.startswith("--track-b="):
                rest.append(x)
        return run_with_track_b(Path(a.track_b), rest, a.install)
    if a.real_model:
        os.environ["VERIHIRE_REAL_MODEL"] = "1"
    if track_b_present():
        os.environ["VERIHIRE_TRACK_B_REQUIRED"] = "1"
    os.chdir(ROOT)
    print(f"{BOLD}VeriHire backend - standalone test run{END}  ({ROOT})")
    tb_state = "PRESENT - integration tests will run" if track_b_present() else "not present - integration tests skipped"
    print(f"Track B (Harsh) code: {tb_state}")
    if not check_deps(a.install):
        return 1
    tmp = TempPostgres()
    try:
        db = get_database(tmp, a.fresh_db)
        if not db:
            return 1
        if not a.skip_lint:
            run_lint()
        run_pytest(db, a.k, a.write_examples)
        if a.skip_smoke or a.k:
            step("live smoke test", "SKIP")
        else:
            run_smoke(db)
    finally:
        if not a.keep_db:
            tmp.stop()
    print(f"\n{BOLD}Summary{END}")
    for name, st, _ in results:
        colour = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW}[st]
        print(f"  {colour}{st:<4}{END}  {name}")
    failed = any(st == "FAIL" for _, st, _ in results)
    print(f"\n{(RED + 'SOME CHECKS FAILED') if failed else (GREEN + 'ALL CHECKS PASSED')}{END}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
