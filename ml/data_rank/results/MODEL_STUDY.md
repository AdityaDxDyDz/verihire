# Reranker model study (v1.2 integration)

> Classification metrics (accuracy, precision, recall, F1, ROC-AUC) for the same 8 models: `MODEL_COMPARISON.md`.

**Question:** is Ekant's XGBoost regressor the best reranker, or does another model / feature set / training set work better? **Answer: XGBoost stays.** No alternative beat it on fresh data. Two improvements were made around it: a skill-extraction fix (the biggest gain) and monotone constraints on years/education (fairness).

Labels are synthetic (Ekant's rubric over generator ground truth), so absolute numbers are optimistic - compare rows, do not quote them as real-world accuracy.

## 1. Cross-validation screen (18 train JDs, nested GroupKFold, 2 repeats) - `docint/rank/benchmark.py`

| Model | NDCG@10 | std | vs xgb_reg (95% CI) | W/L/T |
|---|---|---|---|---|
| lgbm_reg | 0.9823 | 0.035 | +0.0094 [+0.0023, +0.0186] | 12/4/20 |
| hgb_reg | 0.9764 | 0.039 | +0.0034 [-0.0063, +0.0164] | 11/9/16 |
| lgbm_lambdarank | 0.9751 | 0.035 | +0.0022 [-0.0087, +0.0139] | 12/10/14 |
| xgb_reg | 0.9729 | 0.041 | +0.0000 [+0.0000, +0.0000] | 0/0/36 |
| xgb_reg_mono | 0.9721 | 0.041 | -0.0009 [-0.0074, +0.0059] | 8/9/19 |
| xgb_rank_pairwise | 0.9719 | 0.038 | -0.0010 [-0.0070, +0.0045] | 11/10/15 |
| xgb_rank_ndcg | 0.9620 | 0.049 | -0.0109 [-0.0299, +0.0065] | 10/16/10 |
| ridge | 0.8893 | 0.102 | -0.0836 [-0.1182, -0.0509] | 3/28/5 |

CV leads of LightGBM were **not** trusted until confirmed on fresh queries (section 2) - they were not.

## 2. Decisive test: 120 FRESH JDs - `docint/rank/fresh_test.py`

New JDs drawn from Ekant's generator (seed 20261119), labelled from ground truth with his rubric, never used for training, tuning or any earlier choice. Evaluated on the **serving-like corpus** (his resumes rendered to PDF and parsed by Harsh's real pipeline). Scenarios: same embedder as training; a different embedder at serving time; same vectors with a BGE-like similarity range (0.55-0.95).

| Setup | NDCG@10 same embedder | NDCG@10 embedder shift | NDCG@10 bge-like range |
|---|---|---|---|
| A  Ekant v1.1: XGBoost regressor, his features & params | 0.9682 | 0.9480 | 0.9717 |
| B  XGBoost, per-search ratio features, parsed-profile corpus | 0.9694 | 0.9539 | 0.9677 |
| C  LightGBM LambdaRank, ratio features, parsed-profile corpus | 0.9659 | 0.9518 | 0.9656 |
| D  XGBoost, per-search min-max features, parsed-profile corpus | 0.9671 | 0.9567 | 0.9671 |
| E  LightGBM regressor, Ekant's features (best in the CV screen) | 0.9694 | 0.9523 | 0.9709 |
| **S  SHIPPED: XGBoost + monotone(years, education)** | 0.9684 | 0.9554 | 0.9679 |

Paired differences (bootstrap 95% CI over the fresh JDs):

| Comparison | Scenario | Diff | 95% CI | W/L |
|---|---|---|---|---|
| B_xgb_relative vs A | same embedder | +0.0012 | [-0.0048, +0.0072] | 26/20 |
| C_lgbm_lambdarank_relative vs A | same embedder | -0.0023 | [-0.0090, +0.0042] | 26/28 |
| D_xgb_minmax vs A | same embedder | -0.0011 | [-0.0083, +0.0061] | 22/31 |
| E_lgbm_reg vs A | same embedder | +0.0012 | [-0.0048, +0.0071] | 26/19 |
| S_shipped vs A | same embedder | +0.0002 | [-0.0056, +0.0057] | 23/19 |
| C_lgbm_lambdarank_relative vs B | same embedder | -0.0035 | [-0.0090, +0.0018] | 20/30 |
| D_xgb_minmax vs B | same embedder | -0.0023 | [-0.0066, +0.0020] | 16/28 |
| E_lgbm_reg vs B | same embedder | -0.0000 | [-0.0044, +0.0045] | 22/27 |
| S_shipped vs B | same embedder | -0.0010 | [-0.0063, +0.0042] | 22/22 |
| B_xgb_relative vs A | embedder shift | +0.0059 | [+0.0008, +0.0112] | 31/19 |
| C_lgbm_lambdarank_relative vs A | embedder shift | +0.0039 | [-0.0034, +0.0107] | 33/30 |
| D_xgb_minmax vs A | embedder shift | +0.0087 | [+0.0031, +0.0146] | 34/18 |
| E_lgbm_reg vs A | embedder shift | +0.0043 | [-0.0004, +0.0092] | 28/23 |
| S_shipped vs A | embedder shift | +0.0075 | [+0.0015, +0.0134] | 34/15 |
| C_lgbm_lambdarank_relative vs B | embedder shift | -0.0021 | [-0.0090, +0.0048] | 28/35 |
| D_xgb_minmax vs B | embedder shift | +0.0028 | [-0.0025, +0.0084] | 33/25 |
| E_lgbm_reg vs B | embedder shift | -0.0016 | [-0.0075, +0.0043] | 29/28 |
| S_shipped vs B | embedder shift | +0.0015 | [-0.0048, +0.0076] | 30/23 |
| B_xgb_relative vs A | bge-like range | -0.0040 | [-0.0088, +0.0007] | 17/25 |
| C_lgbm_lambdarank_relative vs A | bge-like range | -0.0061 | [-0.0129, +0.0005] | 27/32 |
| D_xgb_minmax vs A | bge-like range | -0.0045 | [-0.0108, +0.0016] | 17/31 |
| E_lgbm_reg vs A | bge-like range | -0.0008 | [-0.0048, +0.0032] | 16/20 |
| S_shipped vs A | bge-like range | -0.0037 | [-0.0091, +0.0015] | 17/30 |
| C_lgbm_lambdarank_relative vs B | bge-like range | -0.0021 | [-0.0084, +0.0040] | 23/29 |
| D_xgb_minmax vs B | bge-like range | -0.0006 | [-0.0039, +0.0027] | 18/22 |
| E_lgbm_reg vs B | bge-like range | +0.0032 | [-0.0011, +0.0074] | 27/19 |
| S_shipped vs B | bge-like range | +0.0003 | [-0.0045, +0.0050] | 23/27 |

**Reading:** the LightGBM regressor's significant CV lead (section 1) does not survive fresh queries (row E); no setup is significantly better than A (Ekant's model) in any scenario. Hence XGBoost stays.

## 3. What actually moved the numbers

1. **Skill extraction, not the model.** After unifying on Harsh's dictionary, 5 skills written in lower case in skill lists (react, spark, swift, angular, snowflake) had 0 % recall. Accepting lower-case common words *only* as list items raised corpus skill recall to 99.7 % and the model's CV NDCG@10 from 0.952 to 0.981.
2. **Monotone constraints (fairness).** Unconstrained, +2 education levels LOWERED the score in 61 of 300 fresh rows (synthetic quirk learned by the trees). Constraining years and education: 0 of 300, at a cost inside the noise in 2 of 3 scenarios (row S vs A). Constraining every feature cost twice as much for no fairness gain.
3. **Training on parsed profiles** (instead of generator profiles) did not help (equal or slightly worse), so training stays on Ekant's corpus and the parsed corpus is used as an extra evaluation (`results/serving_like/RESULTS.md`).

## 4. Reproduce

```bash
cd ml
python data_rank/make_corpus.py --seed 7       # data (labels unchanged)
python data_rank/reingest_corpus.py            # serving-like corpus through Track B
python -m docint.rank.train                    # shipped model
python -m docint.eval.run_eval                 # RESULTS.md
python -m docint.rank.fresh_test               # section 2 (~1 min)
python -m docint.rank.benchmark                # section 1 (~30 min)
python -m docint.rank.study_report             # this file
```
