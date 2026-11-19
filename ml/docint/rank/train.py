"""E6: train the XGBoost reranker.

python -m docint.rank.train            (run from ml/ with PYTHONPATH=ml or `pip install -e ml`)

Protocol (written down so the report and viva can defend it):
  * Train on TRAIN JDs only. The 6 held-out test JDs are never loaded here (leakage test checks it).
  * GroupKFold by JD (a JD never appears in both fit and validation parts of a fold).
  * Small grid chosen by mean validation NDCG@10 over the folds; early stopping uses the fold's
    validation part, which still lies inside the training JDs.
  * Final model: refit on all training JDs with n_estimators = mean best iteration + 1.

v1.2 (integration): MONOTONE CONSTRAINTS on years and education. Unconstrained, the model learned from synthetic
quirks that a higher degree can LOWER the score (61 of 300 fresh-JD rows: +2 education levels -> lower score).
XGBoost now enforces: more years or a higher degree never decrease the score. Measured on 120 fresh JDs
(never used for any choice): 0/300 such rows; NDCG@10 -0.001 (95% CI [-0.006, +0.003]) same embedder,
-0.003 [-0.008, +0.002] with a BGE-like similarity range, -0.005 [-0.010, -0.000] with a different embedder.
A small, documented price for a ranking a recruiter can trust. Model family and features are unchanged: the
120-JD study (docint/rank/fresh_test.py) found no alternative (LightGBM LambdaRank, XGBRanker, other feature
treatments) that beats this XGBoost regressor.
"""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold

from docint.eval.metrics import ndcg_at_k
from docint.rank.data import DATA_DIR, build_dataset, load_jds, load_labels
from docint.rank.features import FEATURE_NAMES, write_schema
from docint.rank.reranker import MODELS_DIR, RERANKER_VERSION
from docint.rank.retriever import ReferenceRetriever, load_corpus

SEED = 7
# +1 = the score may only go UP when this feature goes up (all other features equal). Only years and education
# are constrained: that is where unconstrained trees produced unfair results (a higher degree lowering the score in
# ~20% of rows). Constraining every feature as well cost twice as much accuracy for no fairness gain.
MONOTONE = {"years_exp": 1, "years_gap": 1, "edu_level": 1, "edu_gap": 1}
GRID = {"max_depth": [2, 3, 4], "learning_rate": [0.05, 0.1], "min_child_weight": [1, 5]}


def _df(X: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(X, columns=FEATURE_NAMES)


def make_model(params: dict, n_estimators: int, early_stopping: int | None = None) -> xgb.XGBRegressor:
    return xgb.XGBRegressor(n_estimators=n_estimators, subsample=0.9, colsample_bytree=0.9, random_state=SEED,
                            objective="reg:squarederror", early_stopping_rounds=early_stopping, n_jobs=2,
                            monotone_constraints=tuple(MONOTONE.get(f, 0) for f in FEATURE_NAMES), **params)


def mean_ndcg10(pred: np.ndarray, groups: np.ndarray, meta: list, labels: dict) -> float:
    scores = []
    for jd in sorted(set(groups)):
        idx = np.where(groups == jd)[0]
        order = idx[np.argsort(-pred[idx], kind="stable")]
        ranked = [meta[i][1] for i in order]
        scores.append(ndcg_at_k(ranked, labels[jd], 10))
    return float(np.mean(scores))


def cv_grid(X, y, groups, meta, labels, n_splits: int = 5) -> list[dict]:
    keys = list(GRID)
    results = []
    gkf = GroupKFold(n_splits=min(n_splits, len(set(groups))))
    for combo in itertools.product(*[GRID[k] for k in keys]):
        params = dict(zip(keys, combo))
        fold_scores, best_iters = [], []
        for tr, va in gkf.split(X, y, groups):
            m = make_model(params, 400, early_stopping=20)
            m.fit(_df(X[tr]), y[tr], eval_set=[(_df(X[va]), y[va])], verbose=False)
            pred = m.predict(_df(X[va]))
            fold_scores.append(mean_ndcg10(pred, groups[va], [meta[i] for i in va], labels))
            best_iters.append(int(m.best_iteration) + 1)
        results.append({"params": params, "cv_ndcg@10": float(np.mean(fold_scores)),
                        "mean_best_iter": int(round(np.mean(best_iters)))})
    return sorted(results, key=lambda r: -r["cv_ndcg@10"])


def fit_final(X, y, params, n_estimators: int) -> xgb.XGBRegressor:
    m = make_model(params, n_estimators)
    m.fit(_df(X), y, verbose=False)
    return m


def train(corpus_path: Path, out_dir: Path, embedder, top_k: int = 50) -> dict:
    jds_all = load_jds()
    train_jds = [j for j in jds_all if j["split"] == "train"]
    test_ids = sorted(j["jd_id"] for j in jds_all if j["split"] == "test")
    labels = load_labels()
    retr = ReferenceRetriever(load_corpus(corpus_path), embedder)
    X, y, groups, meta = build_dataset(retr, train_jds, labels, top_k=top_k)
    assert not (set(groups) & set(test_ids)), "LEAK: test JD in training data"
    grid = cv_grid(X, y, groups, meta, labels)
    best = grid[0]
    model = fit_final(X, y, best["params"], best["mean_best_iter"])
    out_dir.mkdir(parents=True, exist_ok=True)
    model.get_booster().save_model(str(out_dir / "xgb_v1.json"))
    write_schema(out_dir / "feature_schema.json")
    report = {"version": RERANKER_VERSION, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "embedder": embedder.name, "n_rows": int(len(y)), "train_jd_ids": sorted(set(groups)),
              "test_jd_ids": test_ids, "best": best, "grid": grid, "top_k": top_k,
              "monotone_constraints": MONOTONE, "corpus": Path(corpus_path).name}
    (out_dir / "xgb_v1.meta.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    from data_rank.make_corpus import StubEmbedder  # stand-in embedder until Harsh's PR #1 merges
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(DATA_DIR / "corpus.jsonl"))
    ap.add_argument("--out", default=str(MODELS_DIR))
    a = ap.parse_args()
    r = train(Path(a.corpus), Path(a.out), StubEmbedder())
    print(f"trained {r['version']} on {r['n_rows']} rows / {len(r['train_jd_ids'])} train JDs")
    print("best:", r["best"])
    print("top 3 grid:", [(g["params"], round(g["cv_ndcg@10"], 4)) for g in r["grid"][:3]])


if __name__ == "__main__":
    main()
