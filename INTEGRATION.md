# Integration log — v1.2 (Raj + Harsh + Ekant merged; Kadam's frontend/deploy still to come)

Every change made while merging the three tracks, **grouped by the owner of the file**, with the reason and the test
that proves it. Each owner should read their section before the viva: you must be able to explain these changes too.

How to verify everything: `python scripts/test_all.py` (see README → Testing).

---

## 1. Bugs found by integration testing (would have broken the merged app)

| # | Problem | Effect if merged as-is | Fixed in | Test |
|---|---|---|---|---|
| 1 | Harsh's `run_ingest` lives in `docint.ingest.ingest`; the plan's contract is `docint.ingest.run_ingest` | backend silently used the **stub** ingester (`ML_MODE=auto`) or refused to start (`ML_MODE=real`) | `ml/docint/ingest/__init__.py` re-exports; backend also looks in sub-modules | `test_ml_adapter.py::test_finds_run_ingest_in_a_submodule` |
| 2 | Track B returns frozen **dataclasses**, the contract is pydantic | every PDF failed with a validation error | backend adapter converts dataclasses/NamedTuples/numpy | `test_dataclass_outputs_none_years_and_foreign_errors` |
| 3 | Track B raises its **own** `ExtractionError` class | corrupt/encrypted PDFs showed as `internal_error` | adapter maps any `ExtractionError`-shaped error to the contract one | `test_integration_track_b.py::test_track_b_extraction_errors_keep_their_code` |
| 4 | `years_experience = None` when a resume has no dates | the whole document failed validation | adapter stores unknown as `0.0` (documented) | `test_failure_drills_with_real_pymupdf` (no-dates PDF) |
| 5 | PyMuPDF is **not thread-safe**; ingestion workers run in parallel threads | random crashes / corrupted text under load | one extraction at a time; lock taken *before* the timeout clock so a hung PDF cannot block the queue | `test_pdf_extraction_is_serialised_but_queue_time_is_not_a_timeout`, 30 docs × 4 workers |
| 6 | Ekant's `RetrievedChunk` is **nested** (`rc.chunk.text_scrubbed`), the backend's is **flat** | reranker crashed → every search fell back to RRF; skill-gap crashed → 502 | contract **v1.2.0**: `RetrievedChunk` accepts both shapes and fills the other | `test_integration_full.py` (all components real) |
| 7 | Ekant's reranker method is `rerank()`, the contract/backend call `rank()` | the trained model was **never used** | `rank()` alias on both reranker classes + backend accepts either | `test_reranker_with_rerank_method_only` |
| 8 | Harsh's `skills_dict.json` is wrapped (`{"schema_version", ..., "skills": {...}}`); Ekant's loader iterated the wrapper, crashed on `schema_version` and **silently** fell back to its own 74-skill list | JD skills and resume skills used **different names** (`kafka` vs `apache kafka`, `vue` vs `vue.js`, `spring` vs `spring boot`) → coverage features wrong | `skillmatch.py` unwraps, delegates matching to Harsh's `extract_skills`, maps legacy names | `test_one_dictionary_is_used`, `test_word_boundaries_and_aliases` |
| 9 | **Hidden white/tiny text** was flagged but still kept in the page text | invisible keyword-stuffing ("Python FastAPI expert, 10 years") became skills, embeddings **and citations** a recruiter cannot see in the PDF | `extractor.py` drops suspicious spans (flag kept) | `test_extractor.py` (hidden words not in text), `test_injection_resume_gets_no_unsupported_skills` |
| 10 | Lower-case common-word skills in lists (`Skills: react, spark, swift`) were never matched | 5 skills had 0 % recall on the training corpus; model CV 0.952 instead of 0.981 | `skills.py`: lower-case accepted **only** as an item of a real list (prose stays strict) | `test_skills.py` (list vs prose cases) |
| 11 | Backend reranked the top **20** retrieved candidates; Track C trains/evaluates on the top **50** | Recall@20 capped near plain-hybrid level (0.56 instead of 0.83) | `RERANK_POOL` default 50 | — (config) |
| 13 | **Windows:** Python's default `ProactorEventLoop` cannot run psycopg in async mode (uvicorn even forces it) | on Windows every DB call timed out after 30 s; server never started | `app/eventloop.py` selector loop used by the server (`--loop app.eventloop:selector_loop`), the tests and the smoke test; the app fails in 1 s with a clear message on a wrong loop | `test_windows_proactor_loop_fails_fast_with_a_clear_message` |
| 14 | Retriever used `np.argsort` (unstable quicksort) on float32 scores | rankings with near-ties differed between Windows and Linux -> reported numbers not reproducible across OSes | `retriever._rank_order`: round to 6 decimals + stable sort; model retrained, all results regenerated | reproducibility step of `test_all.py` |
| 12 | With an API key but no `LLM_MODEL_*` set, Track C silently used its rule-based StubLLM | demo would claim "Claude" while running rules | `/health` now reports `llm_model` and `ml_components.llm = stub` honestly; `.env.example` lists the model ids | `test_rule_based_llm_is_reported_as_stub` |

---

## 2. Changes by owner

### Aditya Raj — backend (`backend/`, `db/`, `scripts/`)
- `app/adapters/ml.py`: multi-location lookup (#1), dataclass/numpy conversion + profile normalisation (#2, #4),
  foreign `ExtractionError` mapping (#3), async PDF lock before timeout (#5), `rank`/`rerank` (#7), honest LLM status
  (#12), **embedding text comes from the ML layer** (`Chunk.embedding_text()` of the object Track B returned).
- `services/ingestion.py`: uses the ML-provided embedding texts; records `PIPELINE_VERSION`.
- `services/search.py`: warning `reranker_model_missing` when Track C falls back to RRF.
- `config.py`: `RERANK_POOL=50` (#11); `ANALYSIS_TIMEOUT_S=90`, `JD_PARSE_TIMEOUT_S=45` (Track C retries the LLM 3×).
- `schemas/api.py`: `HealthResponse.llm_model` (additive, optional).
- Tests added: `test_integration_track_b.py` (9), `test_integration_full.py` (8, every component real, simulated Claude API),
  more adapter tests. `devtools/synthpdf.py`: real AES-encrypted test PDF when PyMuPDF is installed.
- `scripts/test_all.py`: the final all-in-one runner. `scripts/test_backend.py`: `--track-b`, `--real-model`.

### Harsh Lal — Track B (`ml/docint/ingest`, `ml/docint/embed`, `ml/data_ingest`)
- `ingest/__init__.py`: re-exports `run_ingest`, `PIPELINE_VERSION`, `ExtractionError`, `IngestResult` (#1).
- `ingest/extractor.py`: suspicious (hidden) spans are **excluded** from the page text, flag unchanged (#9) — one line.
- `ingest/skills.py`: `_lowercase_list_item_ok()` — all-lower-case common words (react, spark, swift, angular, snowflake…)
  accepted only when they stand alone as a list item on a line with list separators (#10). Prose rules unchanged
  ("we react to feedback", "I spark joy, swift and kind" → nothing).
- Tests moved to `ml/tests/ingest/`; imports changed from `ml.docint…` to `docint…` (one import name = no duplicate classes).
  Two assertions added (hidden text removed; list rule). All 109 original tests still pass; all gold-set targets still met.
- `data_ingest/*`: `ml.` import prefixes removed; `__init__.py` added.

### Ekant Patel — Track C (`ml/docint/rank`, `ml/docint/llm`, `ml/docint/eval`, `ml/data_rank`, `ml/models`)
- `docint/contracts.py` (his draft) **removed**; the shared contract is used (v1.2.0 accepts his nested `RetrievedChunk`).
- `rank/skillmatch.py`: uses Harsh's dictionary and matcher; `LEGACY_NAMES` maps `spring → spring boot`; `IMPLIES`
  canonicalised on load (#8). API unchanged (`find_skills`, `canonicalize`, `expand_implied`, `load_skills`).
- `rank/reranker.py`: `rank()` alias (#7). Everything else is his original code.
- `rank/train.py`: **monotone constraints** on `years_exp`, `years_gap`, `edu_level`, `edu_gap` (see §3). Everything else
  (grid, GroupKFold, early stopping, leakage asserts) is his original protocol.
- `data_rank/make_corpus.py`: labels canonicalise both sides through the one dictionary. **Labels, JDs and resume texts are
  byte-identical** to his originals (checked); only profile skill *names* changed.
- `data_rank/corpus*.jsonl`, `models/xgb_v1.*`, `data_rank/results/*`: regenerated / retrained with the integrated code.
- New (model study, see §3): `data_rank/reingest_corpus.py`, `rank/benchmark.py`, `rank/fresh_test.py`.
- Tests: `test_skillmatch_corpus.py` expectations use the unified names (`spring boot`, `github actions`) + one new test;
  `test_reranker_leakage_parity.py` + one new monotonicity test. All his other assertions unchanged.

---

## 3. Model refinement — what was tried and what was chosen

Question asked: *refine the model to its best; replace XGBoost if something works better.*

**Answer: XGBoost stays.** Nothing beat Ekant's XGBoost regressor on fresh data. Two real improvements were made around it.

| Step | Finding | Decision |
|---|---|---|
| 8 model families, nested CV on the 18 train JDs (`rank/benchmark.py`) | LightGBM LambdaRank / LightGBM regressor led in CV by ≤ 0.01 NDCG@10; CIs included 0 | test on fresh data before believing it |
| **120 fresh JDs** from Ekant's generator, never used for any choice (`rank/fresh_test.py`) | LambdaRank −0.010 vs XGBoost (CI excludes 0) at first; on final data every alternative ties or loses | **keep XGBoost** |
| Feature variants (per-search ratio, min-max, drop similarities) under a different embedder and a BGE-like similarity range | none better; min-max/LambdaRank significantly worse under the BGE-like range | **keep Ekant's 15 features** |
| Train on PDF-parsed (serving-like) profiles instead of generator profiles | equal or slightly worse | keep generator corpus for training; **use the parsed corpus as an extra evaluation** |
| Skill extraction recall on the corpus | 5 skills at 0 % recall (lower-case list items) | **fix the matcher (#10)** → CV 0.952 → 0.981 |
| Unconstrained model: does a higher degree ever lower the score? | **yes, in ~20 % of rows** | **monotone constraints on years + education** — 0 % such rows; NDCG cost −0.001…−0.005, CIs include 0 in 2 of 3 scenarios |

Full numbers: `ml/data_rank/results/MODEL_STUDY.md`, `fresh_test.json`, `benchmark.json`.

---

## 4. Contract changes (v1.1.0 → v1.2.0, additive only)
- `RetrievedChunk`: accepts flat **or** nested (`chunk: Chunk`) shape; the validator fills in the other.
- `Chunk.embedding_text()` returns `text_scrubbed` (matches Track B; the backend uses the ML object's own method anyway).
- `Reranker` protocol: `rank()` is the contract name; `rerank()` accepted.
- `HealthResponse.llm_model` (optional).
Requires the usual four approvals when you open the PR (plan §5.4).

## 5. Not verifiable in this environment (do these once on a laptop with internet)
- The real **BGE-small** model (download blocked here). Harsh's embedder code ran with a deterministic stand-in model.
  Run `python scripts/test_all.py --real-model` once; then retrain with real vectors:
  `cd ml && python -m docint.rank.train && python -m docint.eval.run_eval --embedder real`.
- The real **Claude API**. Ekant's real-LLM code path ran against a simulated `anthropic.Anthropic`; set
  `ANTHROPIC_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_STRONG` and run the smoke test once.

## 6. v1.3 — Kadam's frontend merged (all additive; nothing existing removed)

Contract mismatches found by validating Kadam's fixtures against the backend models, and how each was resolved:

| # | Mismatch | Resolution |
|---|---|---|
| 1 | UI polls the job until `status` leaves `running`; backend said `completed` even with failures | `JobStatus = running \| completed \| completed_with_errors` (+ `created_at`, `finished_at`); smoke/seed updated |
| 2 | Library columns years / education / skills not in `DocumentItem` | joined from the parsed candidate; `counts.processing` added; `status=processing\|all` filters |
| 3 | JD list: backend returned `{items}` of full JDs, UI expected an array with `required_skills`, `last_search_id` | backend keeps `{items}` and adds both fields; `api.js` / `mock.js` accept either shape |
| 4 | Results page needs the JD of a search | `SearchResponse.jd_id` |
| 5 | Drawer shows one e-mail / phone after reveal | `PiiOut.email`, `phone` (first of the lists) alongside the lists |
| 6 | Candidate header shows years / education | flat `years_experience`, `education_level` on `CandidateResponse` |
| 7 | `frontend/config.js` had to be edited per environment (risk of committing keys) | backend generates `GET /config.js` from `.env` (`dev` or `supabase` + anon key only — never the service key) |
| 8 | Fixture generator: numeric chunk ids, missing `error_code` / `size_bytes` / `updated_at`, health `db` | `scripts/make_fixtures.py` fixed; fixtures regenerated in both folders (kept identical by a test) |

Found by the new full-stack browser test / real-data importer:

| # | Problem | Fix |
|---|---|---|
| 9 | **One-paragraph JDs** (job boards, Kaggle CSVs) were read as one giant title → 0 required skills, every score ≈ 0 | Track C rule parser (`ml/docint/llm/jd_rules.py`): long lines split into sentences, inline headings (`Requirements: …`) honoured, a first line is a title only if short; education counted only from required lines ("master's is a plus" ≠ minimum). 3 new tests; gold numbers unchanged |
| 10 | Lint covered Kadam's generator for the first time (long literal rows) | per-file E501 ignore for `scripts/make_fixtures.py` |

New: `scripts/e2e_real.py` (Chrome drives real UI + server + DB + every ML component, 10 checks, fails on any
console error or failed app request), `scripts/import_kaggle.py` (real-dataset import + report), `docs/TESTING_IRL.md`;
`test_all.py` gained 3 frontend steps (node unit tests, mock E2E, full-stack E2E). Not copied from Kadam's zip:
`contract_drafts/` and `tests/test_fixtures_match_models.py` (superseded by `docs/contracts/` and
`backend/tests/test_contract_fixtures.py`).

## 7. v1.3.1 — model comparison with classification metrics

`ml/docint/rank/compare_models.py` → `ml/data_rank/results/MODEL_COMPARISON.md` (+ `.json`, `.png`): the 8 benchmark models
and the shipped file, trained identically, scored on 6 held-out and 120 fresh JDs with accuracy, precision, recall, F1,
ROC-AUC (suitable = grade ≥ 2, threshold tuned on out-of-fold train predictions) and NDCG@10, MRR, P@10, R@10, with
paired bootstrap CIs. Finding: tied with the best on ranking; unconstrained boosted regressors are ~0.02 F1 higher
(the monotone fairness constraint's cost) — documented, model unchanged.

## 8. v1.3.2 — found by the first run with the real BGE model (on Raj's laptop)

* Two integration tests asserted that the planted "ideal" resume ranks exactly #1. A random resume in the same set
  (seed 104: backend, 6 years, FastAPI + PostgreSQL) is equally qualified; with real BGE vectors it ranked first.
  The tests now require the ideal candidate in the top 2 and every candidate above it to meet all required skills.
* That exposed a real inconsistency: the result chips (`matched_required` / `missing_required`) matched skill names
  literally, while the reranker counts implied skills, so the #1 candidate showed "python missing". The chips now
  use Track C's `expand_implied` (backend `services/search.py`, test added). The SQL `required_skills` filter is
  still literal (needs a migration; listed in `docs/components/backend.md`).

## 9. v1.3.3 — testing with LiveCareer resumes + a JD dataset (non-tech roles)

A dry run in the layout of snehaanbhawal/resume-dataset (category folders) + a separate JD CSV showed two gaps:
* **Non-tech skills unknown** (Accountant JD → only `excel`; HR resumes outranked accountants). Harsh's dictionary
  gained 92 domain skills (finance, HR, sales/marketing, healthcare, teaching, hospitality, construction, design,
  legal) - multi-word / specific terms only, so prose like "accounting for 30% of revenue" matches nothing. Track B
  gold targets and Track C committed numbers unchanged; new unit test.
* **JD with no recognised skills → every score ≈ 0** (the reranker never saw such a JD in training). Search now ranks
  by text relevance for those, `reranker_version = retrieval-order-no-required-skills`, and returns a
  `no_required_skills` warning the UI shows as a banner (with the existing *Edit requirements* link). Test added.
* `scripts/import_kaggle.py`: `--jd-csv` (JDs from another dataset), `--jd-match`, spread sampling, and a
  **category check** (share of a JD's top 10 from the resume category its title points to).
Dry-run category check: 78 % → 90 % after the dictionary change (Accountant 30 % → 80 %).

## 10. v1.3.5 — structured JD datasets
* `import_kaggle.py` reads CSV and Excel and, when a JD dataset has no single text column (e.g. adityarajsrv/
  job-descriptions-2025: Title / ExperienceLevel / YearsOfExperience / Skills / Responsibilities / Keywords),
  composes a normal JD from the fields (skills under "Requirements", responsibilities and keywords not mined).
* Track C JD rules: a year range ("2-5 years", "3 to 5 yrs") now gives the minimum (was the upper number). Test added;
  committed numbers unchanged.
* Startup on a database without tables now stops with a plain message (run `scripts/migrate.py --local-shim` or set
  `AUTO_MIGRATE=true`) instead of `relation "documents" does not exist`; `migrate.py` reads `DATABASE_URL` from `.env`.
