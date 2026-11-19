# VeriHire backend (owner: Aditya Raj)

FastAPI + Postgres/pgvector service for VeriHire. Fact-sheet for the report: `docs/components/backend.md`.
Whole-project overview and the all-in-one test runner (`scripts/test_all.py`): see the root `README.md`.

## Test everything with one command

```bash
pip install -r backend/requirements-dev.txt
python scripts/test_backend.py
```

The runner checks your dependencies, finds or **starts** a Postgres + pgvector test database (Docker, or a local
Postgres install), runs ruff, runs all pytest suites with coverage (≥ 70 % required; currently about 95 %), then
starts the real server with uvicorn and runs `scripts/smoke_test.py` against it. It ends with a PASS/FAIL summary,
and exits with code 0 only if every step passed.

| Option | Use |
|---|---|
| `--install` | pip-install missing packages first |
| `-k search` | run only tests matching an expression |
| `--skip-smoke` / `--skip-lint` | skip those steps |
| `--fresh-db` | ignore existing databases and use a throw-away one |
| `--write-examples` | regenerate `docs/contracts/examples_backend/*.json` (real responses for Kadam) |
| `--track-b <folder>` | test against Harsh's handoff **before merging** (copies the repo to a temp dir and lays his `ml/docint/ingest` + `embed` over it) |
| `--real-model` | Track B tests use the real BGE model (needs internet once, or `FASTEMBED_CACHE_PATH` with the model) |

Once Harsh's code is merged into the repo, the plain `python scripts/test_backend.py` runs the Track B integration
tests automatically (and fails, rather than skips, if his code cannot be imported).

Without the runner: `docker compose -f db/docker-compose.yml up -d`, then
`cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/verihire_test pytest -q`.
The test database name must contain `test`, because its schema is wiped at the start of a run.

## Run the server locally

```bash
docker compose -f db/docker-compose.yml up -d                     # Postgres + pgvector on :5432
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/verihire
python scripts/migrate.py --local-shim                            # create tables (idempotent)
uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --reload                   # http://localhost:8000/docs
python scripts/seed_demo.py                                        # 117 fictional resumes + 3 JDs
```

In Swagger, click **Authorize** and paste `dev:you@example.com`.

## Folder structure (files I own or created)

```
verihire/
├── backend/                         # ── Raj ──────────────────────────────────────────────
│   ├── app/
│   │   ├── __init__.py              # falls back to ../ml if docint is not pip-installed
│   │   ├── main.py                  # app factory, lifespan (pool, ML, workers), routers, static frontend
│   │   ├── config.py                # settings from .env; refuses unsafe production config
│   │   ├── auth.py                  # dev tokens + Supabase token verification (cached)
│   │   ├── db.py                    # async psycopg pool, no prepared statements (pooler-safe)
│   │   ├── errors.py                # {"error": {code, message}} envelope, 404-not-403 helpers
│   │   ├── logging_setup.py         # JSON logs, X-Request-ID, security headers
│   │   ├── ratelimit.py             # per-user token bucket for LLM endpoints
│   │   ├── deps.py                  # FastAPI dependency getters
│   │   ├── adapters/ml.py           # THE ONLY place that imports docint implementations (ML_MODE)
│   │   ├── schemas/api.py           # SHARED/FROZEN: every request/response model
│   │   ├── routers/                 # health, me(+audit), documents(+jobs, files), job_descriptions,
│   │   │                            #   search(+csv), analysis, candidates(+feedback)
│   │   └── services/
│   │       ├── documents.py         # upload validation, library, jobs, delete, retry, pdf links
│   │       ├── ingestion.py         # queue workers, claim/fencing, heartbeat, janitor
│   │       ├── storage.py           # LocalStorage (HMAC links) / SupabaseStorage
│   │       ├── jobdesc.py           # JD create/parse/edit
│   │       ├── search.py            # hybrid SQL -> aggregation -> rerank -> explained results
│   │       ├── analysis.py          # skill-gap + cache keyed by JD-spec hash
│   │       ├── citations.py         # backend citation verification + exact offsets
│   │       ├── candidates.py        # blind profile, audited PII reveal, audit list
│   │       ├── feedback.py, csv_export.py, audit.py
│   ├── devtools/synthpdf.py         # dependency-free fictional resume PDF generator (tests/seed)
│   ├── tests/                       # 177 tests incl. Track B + full-stack integration (see table below)
│   ├── requirements.txt, requirements-dev.txt, pytest.ini, ruff.toml   (settings: root .env.example)
│   └── README.md
├── db/                              # ── Raj ──
│   ├── migrations/001_init.sql      # tables + indexes (frozen at G1)
│   ├── migrations/002_hybrid_fn.sql # hybrid_search_chunks() with RRF (rerunnable)
│   ├── migrations/003_lockdown.sql  # RLS on, no policies, anon/authenticated revoked (rerunnable)
│   ├── local/auth_shim.sql          # local copies of Supabase roles
│   ├── supabase/storage_policies.sql# private 'resumes' bucket (production only)
│   └── docker-compose.yml           # local Postgres + pgvector
├── scripts/
│   ├── migrate.py                   # ── Raj ── idempotent migrations
│   ├── test_backend.py              # ── Raj ── ONE command to test the backend alone
│   ├── smoke_test.py                # end-to-end check of a running server (local or deployed)
│   └── seed_demo.py                 # loads >= 100 demo resumes through the real API
├── docs/
│   ├── components/backend.md        # ── Raj ── fact-sheet for report ch. 5
│   └── contracts/examples_backend/  # real API responses + openapi.backend.json (for Kadam)
└── ml/                              # SHARED drafts (Foundation PR #0) - keep the team's copy if it exists
    ├── pyproject.toml
    └── docint/__init__.py, contracts.py, stubs.py
```

## Tests

| File | What it proves |
|---|---|
| `test_unit_pure.py` | config safety rules, ts_query sanitising, file-name cleaning, CSV injection, rate limiter, citation matching (no DB) |
| `test_core_api.py` | health, dev + Supabase auth (mocked, cached, outage → 503), error envelope, request id, OpenAPI, static frontend, AUTO_MIGRATE |
| `test_storage.py` | traversal rejection, HMAC links (tamper/expiry), Supabase storage calls (mocked) |
| `test_documents.py` | upload rules (type, size, count, empty, duplicates, `files[]`), library paging/filter/search, jobs, delete cascade, retry |
| `test_ingestion.py` | corrupt/encrypted/scanned/hidden-text PDFs, PII scrubbed, 8 workers × 50 docs without double work, crash-restart recovery, janitor, zombie fencing, timeouts, wrong embedding size |
| `test_search_analysis.py` | correct candidate ranked first, filters, hostile/empty queries, reranker failure fallback, stale embeddings, CSV, blind mode, audited reveal, verbatim citations, fabricated/foreign citations dropped, cache invalidation, 429/504/502, feedback, tenant isolation |
| `test_ml_adapter.py` | ML_MODE stub/auto/real, `@unimplemented`, broken teammate module, dimension check |
| `test_db_sql.py` | migrations idempotent + edit guard, RLS blocks `anon`, RRF arithmetic, filters, EXPLAIN timing |
| `test_integration_track_b.py` | **my backend + Harsh's real ingest/embed code**: backend finds his entry points, his dataclasses convert, his `ExtractionError` codes survive, real PyMuPDF on corrupt/encrypted/scanned/hidden-text/no-dates PDFs, no PII in chunks, 384-d unit vectors, scrubbed text embedded without prefix and queries with the BGE prefix, 30 docs × 4 workers, search + verified citations on his chunks |
| `test_integration_full.py` | **every teammate component real** (`ML_MODE=real`): Track C parser + XGBoost reranker with 15 SHAP "why" values, hidden-text resume never wins, skill-gap with double citation verification, simulated Claude API (forced tool use, hallucination dropped), missing-model fallback warning |
| `test_contract_fixtures.py` | Kadam's `docs/contracts/fixtures/*.json` validate against `api.py`; every route has a response model |

## Notes for teammates

* **Kadam (frontend/deploy):** the upload field can be `files` or `files[]`. The upload returns **202**.
  `display_name` appears only when `blind=false`. Real responses are in `docs/contracts/examples_backend/`.
  Start command: `uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --host 0.0.0.0 --port 7860`. For production set
  `APP_ENV=production AUTH_MODE=supabase STORAGE_MODE=supabase ML_MODE=real` plus the Supabase keys and a 32+ character
  `APP_SECRET` (the app refuses to start otherwise). Use the pooler `DATABASE_URL`; `AUTO_MIGRATE=true` is safe.
  CI: run `python scripts/test_backend.py --skip-smoke` with a `pgvector/pgvector:pg16` service and
  `TEST_DATABASE_URL` pointing at it.
* **Harsh:** tested against your handoff - it works with this backend. Please still add
  `from .ingest import run_ingest, PIPELINE_VERSION, ExtractionError` to `ml/docint/ingest/__init__.py` (the backend
  now also looks in `docint.ingest.ingest`, but the plan's contract is the package path). Expected:
  `docint.ingest.run_ingest(pdf_bytes, filename) -> (ExtractedDoc, IngestResult)` and
  `docint.embed.get_embedder()` (an object with `.name`, `.dim == 384`, `embed_documents`, `embed_query`). Raise
  `ExtractionError(code)` for bad files. Expose `PIPELINE_VERSION`. Your skills must be lowercase canonical names.
* **Ekant:** `docint.rank.build_queries(job)`, `docint.rank.get_reranker()` (an object with `.version` and
  `.rank(job, list[CandidateEvidence]) -> list[RankedCandidate]`), and in `docint.llm`: `get_llm_client()`,
  `parse_job_description(text, llm)`, `analyze_skill_gap(job, profile, chunks: list[RetrievedChunk], llm)`, and
  `PROMPT_VERSION`. The backend re-verifies your citations, so a bad quote is dropped, not shown.
