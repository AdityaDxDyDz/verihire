# Backend — component fact-sheet (Aditya Raj)

**Purpose.** One FastAPI service that owns all data: it accepts resume PDFs, runs the ingestion pipeline in the
background, stores chunks + embeddings in Postgres/pgvector, answers searches with hybrid retrieval + reranking,
runs the LLM skill-gap analysis, and **re-verifies every citation** before anything reaches the browser.
It also serves the static frontend from the same origin (no CORS).

**Stack.** Python 3.11+, FastAPI, psycopg 3 (async pool, raw SQL), Postgres 16 + pgvector, pydantic v2,
httpx. ML is called only through `backend/app/adapters/ml.py`.

## Data flow

```
upload (multipart) ──► validate (%PDF magic, ≤10 MB, ≤100 files, SHA-256 dedupe)
                     ──► storage (local or private Supabase bucket) ──► documents row = queue item
worker (N per container) ── claim (FOR UPDATE SKIP LOCKED) ──► run_ingest (one at a time: PyMuPDF
                     is not thread-safe) ──► embed (384-d, text chosen by Track B's Chunk.embedding_text())
                     ──► ONE transaction: candidate + PII (separate table) + chunks + status=ready
search ──► JD spec ──► build_queries ──► embed query (LRU-cached) ──► hybrid_search_chunks (SQL, RRF k=60)
       ──► group by candidate (rrf_sum_top3, rrf_max, n_chunks_hit) ──► top 50 ──► reranker (Track C XGBoost)
       ──► explained results (matched/missing skills, "why" contributions) ──► search_runs
analysis ──► cache? ──► analyze_skill_gap (LLM) ──► backend citation check ──► analyses (cache)
```

## Ingestion state machine

`uploaded → extracting → embedding → ready` (or `failed` with an `error_code`, or `needs_ocr` for scanned PDFs).

* **No double processing:** the claim is a single `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`.
  Tested with 8 concurrent workers on 50 documents: every document has `attempts = 1`.
* **Fencing:** every later write checks `claimed_by = me`; a slow "zombie" worker cannot overwrite a document
  that the janitor reassigned or the user deleted.
* **Crash recovery:** workers send a heartbeat every 10 s. The janitor returns documents with a stale heartbeat
  to `uploaded` (`failed: timeout` after 3 attempts). On startup, documents left in flight by the killed process
  are reclaimed at once (tested by simulating a crash and restarting).
* **Idempotent reprocess:** retry replaces chunks, PII and cached analyses in one transaction; the candidate id is kept.
* Every ready document records the embedder used, and search warns if vectors from different models are mixed.

## Database (db/migrations)

`ingestion_jobs, job_documents, documents, candidates, candidate_pii, chunks (vector(384) + generated tsvector,
HNSW + GIN), job_descriptions, search_runs, analyses, feedback, audit_log`.
RLS is on with **no policies** and `anon`/`authenticated` are revoked, so only the backend can read data.
`scripts/migrate.py` is idempotent (checksums, advisory lock, refuses edited migrations, re-runs files marked
`-- migrate: rerunnable`) and works through the Supabase pooler.

**`hybrid_search_chunks`** scores the caller's eligible chunks twice (cosine distance and `ts_rank_cd`) and fuses
the ranks: `rrf = Σ 1/(60 + rank)`. The vector side is an **exact** scan over the tenant's chunks: an HNSW scan
with a tenant filter only post-filters `ef_search` rows and can silently return nothing. Measured: 5,610 chunks,
SQL 29 ms, API p95 70 ms (target was < 3 s).

## Security and privacy decisions

| Concern | Decision |
|---|---|
| Auth | Supabase token verified by calling `/auth/v1/user` (works for any signing method, honours revoked sessions), cached 5 min by token hash. `dev:<email>` tokens only when `AUTH_MODE=dev`; the app refuses to boot in production with dev auth, local storage, stub ML or a weak secret. |
| Tenancy | Every query filters on `owner_id`; foreign or malformed ids return **404, not 403** (existence is never leaked). Tested for all 9 object routes. |
| Blind mode | PII is stored in `candidate_pii` only. Search, candidate and analysis responses carry no name/e-mail/phone (asserted by regex in tests). `reveal=true` and `blind=false` write audit rows; `GET /audit` shows them. |
| Citations | The backend never trusts the ML layer: a citation survives only if its quote is found (whitespace/quote/case-tolerant) inside a chunk **of the same candidate**. The returned quote is the exact span, so `text_scrubbed[start:end] == quote` always holds. Unsupported skills are downgraded to `missing`; drops are counted. |
| Files | Storage keys are generated (`<owner-hash>/<uuid>.pdf`), never from file names; traversal-checked. PDFs are opened via short-lived signed URLs (HMAC for local storage). |
| Abuse | Per-user token-bucket limit on LLM endpoints (429 + `Retry-After`), CSV export neutralises `= + - @` cells, `to_tsquery` input is sanitised in the backend too. |

## Resilience

Reranker crash → retrieval-order fallback + `warnings[]`. Real JD parser failure → rule-based parser.
LLM timeout → 504 with a clear code. Unknown API path → JSON 404. Every error uses
`{"error": {"code", "message"}}`, and every response carries `X-Request-ID` (also in JSON logs).

## Integration with Track B / Track C (v1.2)

The adapter accepts what the teammates actually ship: entry points in sub-modules, dataclasses instead of pydantic,
their own `ExtractionError`, `rerank()` instead of `rank()`, `years_experience=None`. `/health` reports each component as
`real`/`stub` and the live `llm_model` (a rule-based LLM is reported as `stub`). Details: `INTEGRATION.md`.

## Frontend contract (v1.3)

The backend serves `frontend/` and generates `GET /config.js` from settings (`mode` dev|supabase, Supabase URL + anon key
only in supabase mode). Additive API fields for the UI: job `completed_with_errors`, `created_at`, `finished_at`; library
`years_experience`, `education_level`, `skills`, `counts.processing`; JD list `required_skills`, `last_search_id`;
`SearchResponse.jd_id`; `PiiOut.email` / `phone`; flat `years_experience` / `education_level` on candidates. Kadam's
fixtures (`docs/contracts/fixtures/`) are validated against these models in `tests/test_contract_fixtures.py`;
`scripts/e2e_real.py` checks the whole flow in a real browser.

## ML_MODE

`stub` = all `docint.stubs`; `auto` = per component, real if importable and not marked `@unimplemented`
(teammates' code is picked up as soon as it merges); `real` = startup fails if anything is missing
(required in production). `/health` reports each component as `real` or `stub`.

## How to run and test

```bash
python scripts/test_all.py              # whole repo: Track B + Track C + backend + integration + live smoke test
python scripts/test_backend.py          # backend only: deps, DB (auto-started), lint, tests, live smoke test
uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --reload          # Swagger UI at http://localhost:8000/docs
python scripts/smoke_test.py --base-url http://localhost:8000
python scripts/seed_demo.py --base-url http://localhost:8000      # ≥ 100 fictional resumes for the demo
```

## Outreach e-mails (v1.4)

See `docs/components/outreach.md`: `services/outreach.py` (batch creation, background sender with retries and
throttle, token-based public reply), `services/mailer.py` (outbox / SMTP, standard library only),
`routers/outreach.py`, migration `004_outreach.sql`.

## Known limits

In-memory rate limiter and query cache (one container). No OCR (`needs_ocr`). Exact vector scan is right up to
roughly 100k chunks per user; beyond that, switch to pgvector ≥ 0.8 iterative index scans.
`match_score` is a relative ranking score (0–100), not a probability.
The `filters.required_skills` search filter (SQL `skills @> …`) matches skill names literally, while the result chips
and the reranker count implied skills (FastAPI ⇒ Python); making the filter implication-aware needs a migration.
