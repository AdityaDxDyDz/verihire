# Model card: VeriHire ranking + LLM layer (Track C, Ekant Patel)

Written from real runs of `ml/run_track_c.sh` (numbers in `ml/data_rank/results/RESULTS.md`).

## v1.2 integration update (read first)
- **Model family unchanged: XGBoost regressor, same 15 features, same training protocol.** A model study
  (`ml/data_rank/results/MODEL_STUDY.md`) compared 8 model families and 3 feature treatments with nested CV and then
  on **120 fresh JDs** never used for any choice: nothing beat this model (LightGBM LambdaRank / regressor tied or lost).
- **Monotone constraints** on `years_exp`, `years_gap`, `edu_level`, `edu_gap`: the unconstrained model lowered the score
  for a higher degree in ~20 % of rows; now never. Cost: within noise in 2 of 3 scenarios (-0.001 to -0.005 NDCG@10).
- **One skills dictionary (Harsh's, 411 skills)** is now really used - the v1.1 loader silently fell back to the
  74-skill list because of the file's wrapper format. Skill-name mismatches (`kafka` vs `apache kafka`) are gone.
- Task **E12 done** (as far as possible offline): the corpus was rendered to PDF and parsed by the real Track B pipeline
  (`data_rank/reingest_corpus.py`); results on that serving-like corpus are in `results/serving_like/RESULTS.md`.
  Training on parsed profiles did not help, so training stays on the generator corpus.
- Still a stand-in embedder (BGE download not possible in the build environment) - retrain once with
  `--embedder real` before quoting hybrid-retrieval numbers.

## What it is
1. **Retrieval** (offline reference implementation of the SQL hybrid search): BM25 + dense vectors merged with
   Reciprocal Rank Fusion (score = sum of 1/(60 + rank)), grouped per candidate (`rrf_sum_top3`, `rrf_max`, `n_chunks_hit`).
2. **Reranker**: XGBoost regressor, 15 features (`ml/docint/rank/features.py`), target = grade / 3 x 100.
   `match_score` is a *relative ranking score 0-100, not a probability*. "Why" bars = XGBoost `pred_contribs`
   (per-feature contributions; contributions + bias = score).
3. **LLM layer**: JD parsing (LLM, then one repair retry, then rule-based fallback) and skill-gap analysis with
   citations. Every citation is verified in code against its own chunk and replaced by the exact original substring;
   unverifiable citations are dropped and statuses without relevant evidence are downgraded.

## Intended use
Shortlisting support for a human reviewer on resumes. Not for automated rejection. Scores are relative within one search.

## Data
Fully synthetic: 360 resumes (7 role families; 79 framework-only "synonym" style, 53 with a coursework-only
distractor skill, 17 with prompt-injection text) and 24 JDs (6 held out as test, chosen by index, never used for tuning).
No real people. See `ml/data_rank/make_corpus.py`, `LABELS.md`.

## Training protocol
Train JDs only (18). GroupKFold by JD for a 12-point grid; selection metric = mean validation NDCG@10; early stopping on the
fold's validation part; final refit on all train JDs. Test JD ids are asserted disjoint in code and tests.

## Results (v1.2, stub embedder; see RESULTS.md for the full tables)
| Held-out test JDs (n=6) | NDCG@10 | MRR | Recall@20 |
|---|---|---|---|
| E0 keyword (BM25) | 0.608 | 0.792 | 0.535 |
| E1 vector only | 0.478 | 0.617 | 0.417 |
| E2 hybrid (RRF) | 0.567 | 0.806 | 0.556 |
| E3 hybrid + XGBoost (monotone) | 0.957 | 1.000 | 0.801 |

CV over all 24 JDs: E0 0.703, E1 0.549, E2 0.638, E3 0.978 (NDCG@10). Serving-like corpus (parsed PDFs): held-out E3 0.963,
CV 0.976. 120 fresh JDs: 0.968 (same embedder), 0.955 (different embedder), 0.968 (BGE-like similarity range).
Rankings are deterministic across operating systems since v1.2.1 (scores rounded to 6 decimals, stable tie-break).
v1.1 reported E3 0.971 held-out on the old corpus; with 6 JDs (std 0.06) the difference is noise - see MODEL_STUDY.md.

## Limitations (read before quoting any number)
- **E3 is close to circular.** Labels come from synthetic ground truth and 80% of the model's gain is `required_skill_coverage`
  (plus `years_gap`, `edu_gap`): the same inputs as the label rubric. The jump from E2 to E3 shows the model re-learned the
  rubric, not that it would rank real resumes this well.
- **The embedder is a stand-in** (hashed bag-of-words + skill-family tokens). It is not semantic. That is why E1 and E2 do *not*
  beat BM25 overall (E0 beats E2 on 15 of 24 JDs). The only place hybrid wins is the synonym test (NDCG@10 0.514 vs 0.426), which
  is the designed advantage. Re-run with BGE-small (`--embedder real`) before concluding anything about hybrid retrieval.
- **No real-LLM numbers yet.** The LLM is a rule-based stub unless `ANTHROPIC_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_STRONG` are set.
  The citation and injection tables measure *our verification code* using test doubles (one simulates hallucinated quotes,
  one obeys injected text), not Claude's behaviour.
- The rule-based JD parser scores 24/24 on generated JDs, which are formulaic. It is not evidence for the >= 90% F1 target on
  15 hand-labelled real JDs.
- Parser does not split mixed bullets ("Docker; Kubernetes is a plus" is classed as one mode per line).
- Retrieval works on top-100 chunks per side; Recall@20 is bounded by how many relevant resumes exist per JD.
- Bias: the model never sees names or other PII (blind by design), but no name-swap or demographic test has been run (P1 item).
- Long-tail: skills outside the dictionary are invisible to features and JD parsing.

## Failure modes
Missing model file -> RRF order (warning from backend). Feature-schema mismatch -> loud error (retrain). LLM down -> rule-based JD
parse; skill-gap returns all-missing with no citations. A resume cannot raise a skill status without a verbatim, relevant quote.

## Reproduce
`cd ml && bash run_track_c.sh` (generates data, re-ingests it through Track B, trains, evaluates, runs the tests).
Whole-repo check: `python scripts/test_all.py` (also asserts every number in results.json is re-computed).
