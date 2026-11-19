#!/usr/bin/env python3
"""FINAL test runner for the integrated VeriHire repo: backend (Raj) + Track B (Harsh) + Track C (Ekant) +
frontend (Kadam).

    python scripts/test_all.py                  # everything
    python scripts/test_all.py --install        # pip install every requirements file first
    python scripts/test_all.py --quick          # skip the slow reproducibility re-run and the live smoke test
    python scripts/test_all.py --real-model     # also use the real BGE-small model (needs internet once)

Steps (each prints PASS / FAIL / SKIP; exit code 0 only if nothing FAILED):
   1. dependencies          backend + ingest + rank requirements importable
   2. test database         Postgres + pgvector found or started (Docker / local initdb) - see test_backend.py
   3. lint                  ruff on backend/ and scripts/ (the code Raj owns)
   4. Track B unit tests    ml/tests/ingest              (Harsh)
   5. Track B quality       python -m data_ingest.evaluate --strict   (gold-set targets met)
   6. Track C unit tests    ml/tests/rank ml/tests/llm   (Ekant; leakage + train/serve parity included)
   7. Track C reproducible  re-run docint.eval.run_eval into a temp dir; numbers must match docs/results
   8. backend + integration backend/tests (API, DB, ingestion, search, analysis, isolation, Track B and
                            full-stack integration with every teammate component REAL, simulated Claude API)
   9. live smoke test       real uvicorn server, ML_MODE=real (or auto without the BGE model) + smoke_test.py
  10. frontend unit tests   node --test frontend/tests/*.test.mjs   (SKIP without Node 18+)
  11. frontend mock E2E     Playwright on the in-browser mock        (SKIP without Playwright + Chromium)
  12. full-stack E2E        Playwright: real UI + real server + real DB + every ML component (scripts/e2e_real.py)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ML = ROOT / "ml"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend as tb  # noqa: E402  (reuses step(), database bring-up, lint, smoke)

REQ_FILES = [ROOT / "backend" / "requirements-dev.txt", ML / "requirements-ingest.txt", ML / "requirements-rank.txt"]
ML_IMPORTS = {"pymupdf": "pymupdf", "fastembed": "fastembed", "numpy": "numpy", "xgboost": "xgboost",
              "sklearn": "scikit-learn", "pandas": "pandas"}


def install_all() -> None:
    for req in REQ_FILES:
        cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)]
        if subprocess.run(cmd).returncode != 0:
            subprocess.run(cmd + ["--break-system-packages"])


def check_ml_deps() -> bool:
    import importlib
    missing = []
    for mod, pip_name in ML_IMPORTS.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        tb.step("ML dependencies", "FAIL", "missing " + ", ".join(missing) + "  ->  rerun with --install")
        return False
    tb.step("ML dependencies", "PASS", "pymupdf, fastembed, xgboost, scikit-learn, pandas")
    return True


def ml_env() -> dict:
    return {**os.environ, "PYTHONPATH": str(ML) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1"}


def run(name: str, cmd: list[str], cwd: Path, env: dict | None = None) -> bool:
    r = subprocess.run(cmd, cwd=cwd, env=env or ml_env())
    tb.step(name, "PASS" if r.returncode == 0 else "FAIL")
    return r.returncode == 0


def run_frontend(db: str, quick: bool, channel: str | None) -> None:
    fe = ROOT / "frontend"
    if not (fe / "index.html").exists():
        tb.step("frontend", "SKIP", "frontend/ not present")
        return
    node = shutil.which("node")
    if node:
        files = sorted(str(f.relative_to(fe)) for f in (fe / "tests").glob("*.test.mjs"))
        run("frontend unit tests (node)", [node, "--test", *files], fe, env=os.environ.copy())
    else:
        tb.step("frontend unit tests (node)", "SKIP", "install Node.js 18+")
    if importlib.util.find_spec("playwright") is None:
        hint = "pip install playwright && python -m playwright install chromium"
        tb.step("frontend mock E2E", "SKIP", hint)
        tb.step("full-stack E2E (browser)", "SKIP", hint)
        return
    _browser_step("frontend mock E2E", [sys.executable, str(fe / "tests" / "e2e_smoke.py")])
    if quick:
        tb.step("full-stack E2E (browser)", "SKIP", "--quick")
        return
    cmd = [sys.executable, str(ROOT / "scripts" / "e2e_real.py"), "--database-url", db]
    _browser_step("full-stack E2E (browser)", cmd + (["--channel", channel] if channel else []))


def _browser_step(name: str, cmd: list[str]) -> None:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 and ("Executable doesn't exist" in out or "playwright install" in out):
        tb.step(name, "SKIP", "run: python -m playwright install chromium   (or pass --channel chrome)")
        return
    print("\n".join(ln for ln in out.splitlines() if "Warning" not in ln and "warnings.warn" not in ln)[-4000:])
    tb.step(name, "PASS" if r.returncode == 0 else "FAIL")


def _compare(old, new, tol: float, tol_std: float = 0.05) -> list[str]:
    """Means/values must agree within ``tol``; a standard deviation over only 6-24 queries moves a lot when ONE
    query changes, so spreads get ``tol_std``. Timings are ignored (they depend on the machine)."""
    diffs: list[str] = []

    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a:
                if k in b and k != "latency":  # timings vary by machine
                    walk(a[k], b[k], f"{path}.{k}")
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            limit = tol_std if path.endswith(".std") else tol
            if abs(a - b) > limit:
                diffs.append(f"{path}: {a} -> {b}")

    walk(old, new)
    return diffs


def check_reproducible(tol: float = 0.02) -> None:
    """Every number in the committed Track C results must come out again from the committed data + model."""
    targets = [("results", ML / "data_rank" / "corpus.jsonl", ML / "data_rank" / "results"),
               ("serving_like", ML / "data_rank" / "corpus_ingested.jsonl", ML / "data_rank" / "results" / "serving_like")]
    problems: list[str] = []
    checked = 0
    for name, corpus, folder in targets:
        committed = folder / "results.json"
        if not committed.exists() or not corpus.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-m", "docint.eval.run_eval", "--embedder", "stub", "--corpus", str(corpus),
                                "--out", tmp], cwd=ML, env=ml_env(), capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2000:], r.stderr[-2000:])
                problems.append(f"{name}: run_eval crashed")
                continue
            new = json.loads((Path(tmp) / "results.json").read_text())
        problems += [f"{name}{d}" for d in _compare(json.loads(committed.read_text()), new, tol)]
        checked += 1
    if not checked:
        tb.step("Track C numbers reproducible", "SKIP", "no committed results")
        return
    tb.step("Track C numbers reproducible", "PASS" if not problems else "FAIL",
            f"{checked} result sets re-computed" if not problems else "; ".join(problems[:5]))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--install", action="store_true")
    p.add_argument("--quick", action="store_true", help="skip reproducibility re-run and live smoke test")
    p.add_argument("--real-model", action="store_true", help="use the real BGE model (needs internet once)")
    p.add_argument("--skip-lint", action="store_true")
    p.add_argument("--fresh-db", action="store_true")
    p.add_argument("--keep-db", action="store_true")
    p.add_argument("--channel", default=None, help="browser for the E2E tests, e.g. chrome or msedge (installed)")
    a = p.parse_args()
    os.chdir(ROOT)
    print(f"{tb.BOLD}VeriHire - FULL integrated test run{tb.END}  ({ROOT})")
    if a.install:
        install_all()
    if a.real_model:
        os.environ["VERIHIRE_REAL_MODEL"] = "1"
        os.environ.pop("VERIHIRE_EMBED_OFFLINE", None)
    for part in ("ingest", "embed", "rank", "llm"):
        if not (ML / "docint" / part / "__init__.py").exists():
            tb.step("repository layout", "FAIL", f"ml/docint/{part} missing - is this the integrated repo?")
            return 1
    os.environ["VERIHIRE_TRACK_B_REQUIRED"] = "1"
    os.environ["VERIHIRE_TRACK_C_REQUIRED"] = "1"
    if not (tb.check_deps(False) and check_ml_deps()):
        return 1
    tmp = tb.TempPostgres()
    try:
        db = tb.get_database(tmp, a.fresh_db)
        if not db:
            return 1
        if not a.skip_lint:
            tb.run_lint()
        run("Track B (Harsh) unit tests", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                            "tests/ingest"], ML)
        run("Track B gold-set quality targets", [sys.executable, "-m", "data_ingest.evaluate", "--strict"], ML)
        run("Track C (Ekant) unit tests", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                            "tests/rank", "tests/llm"], ML)
        if a.quick:
            tb.step("Track C numbers reproducible", "SKIP", "--quick")
        else:
            check_reproducible()
        tb.run_pytest(db, None, False)
        if a.quick:
            tb.step("live smoke test", "SKIP", "--quick")
        else:
            tb.run_smoke(db)
        run_frontend(db, a.quick, a.channel)
    finally:
        if not a.keep_db:
            tmp.stop()
    print(f"\n{tb.BOLD}Summary{tb.END}")
    for name, st, detail in tb.results:
        colour = {"PASS": tb.GREEN, "FAIL": tb.RED, "SKIP": tb.YELLOW}[st]
        print(f"  {colour}{st:<4}{tb.END}  {name}{(' - ' + detail) if detail and st != 'PASS' else ''}")
    failed = any(st == "FAIL" for _, st, _ in tb.results)
    print(f"\n{(tb.RED + 'SOME CHECKS FAILED') if failed else (tb.GREEN + 'ALL CHECKS PASSED')}{tb.END}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
