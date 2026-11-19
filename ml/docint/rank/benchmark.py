"""Model-selection benchmark for the reranker (integration step, v1.2).

python -m docint.rank.benchmark            (from ml/, ~5-10 min on a laptop)

Question: is XGBoost-regression (Ekant's E3) the best choice, and with which features / training data?

Protocol (no test-JD leakage):
  * Only the 18 TRAIN JDs are used for every choice. Outer GroupKFold (6 folds x REPEATS shuffles) by JD;
    inside each outer-train part a small grid is chosen by inner 3-fold GroupKFold NDCG@10.
  * Scores = NDCG@10 per held-out-fold JD (labels over the WHOLE corpus, as in run_eval).
  * Paired comparison vs the baseline on the same folds: mean diff, wins/losses, bootstrap 95% CI.
  * The 6 held-out TEST JDs are evaluated ONCE at the end, only for the final choice vs the original model.

Scenarios:
  in-dist    train + test on the re-ingested corpus (serving-like profiles), same embedder
  emb-shift  train with embedder A (Track C stub), test with embedder B (docint.stubs hash embedder):
             a stand-in for "trained on stub vectors, served with BGE" - similarity values move.
  skew       train on the ORIGINAL perfect-profile corpus, test on the re-ingested one (what serving sees).
"""
from __future__ import annotations

import itertools
import json
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

from docint.eval.metrics import mrr, ndcg_at_k
from docint.rank.data import DATA_DIR, TARGET_SCALE, build_dataset, load_jds, load_labels
from docint.rank.features import FEATURE_NAMES
from docint.rank.retriever import CorpusDoc, ReferenceRetriever, load_corpus

warnings.filterwarnings("ignore")
SEED, REPEATS, TOP_K = 7, 2, 50
OUT = DATA_DIR / "results" / "benchmark.json"
REL_FEATURES = ["rrf_sum_top3", "rrf_max", "best_vec_sim", "mean_vec_sim"]
MONO = {"required_skill_coverage": 1, "preferred_skill_coverage": 1, "n_required_missing": -1,
        "years_gap": 1, "edu_gap": 1, "experience_section_hit": 1}


# ------------------------------------------------------------------ feature treatments
def transform(X: np.ndarray, groups: np.ndarray, how: str) -> np.ndarray:
    """abs = Ekant's features (SHIPPED); rel = similarity/RRF features divided by the query's max;
    nosim = vector-similarity features removed."""
    X = X.copy()
    idx = [FEATURE_NAMES.index(n) for n in REL_FEATURES]
    if how == "minmax":  # (v - min) / (max - min) per search: invariant to any scale AND offset of the similarities
        for g in np.unique(groups):
            rows = groups == g
            for j in idx:
                lo, hi = np.min(X[rows, j]), np.max(X[rows, j])
                X[rows, j] = (X[rows, j] - lo) / (hi - lo) if hi > lo else 0.0
        return X
    if how == "rel":
        for g in np.unique(groups):
            rows = groups == g
            for j in idx:
                m = np.max(np.abs(X[rows, j]))
                X[rows, j] = X[rows, j] / m if m > 0 else 0.0
    elif how == "nosim":
        X[:, [FEATURE_NAMES.index("best_vec_sim"), FEATURE_NAMES.index("mean_vec_sim")]] = 0.0
    return X


# ------------------------------------------------------------------ models
@dataclass
class Spec:
    name: str
    grid: dict
    kind: str  # "reg" (target 0-100) or "rank" (graded labels + query groups)


def _qid(groups: np.ndarray) -> np.ndarray:
    _, inv = np.unique(groups, return_inverse=True)
    return inv


def make(spec: Spec, p: dict):
    import lightgbm as lgb
    import xgboost as xgb
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    common_x = dict(subsample=0.9, colsample_bytree=0.9, random_state=SEED, n_jobs=-1, tree_method="hist")
    if spec.name == "xgb_reg":
        return xgb.XGBRegressor(objective="reg:squarederror", **common_x, **p)
    if spec.name == "xgb_reg_mono":
        cons = tuple(MONO.get(f, 0) for f in FEATURE_NAMES)
        return xgb.XGBRegressor(objective="reg:squarederror", monotone_constraints=cons, **common_x, **p)
    if spec.name == "xgb_rank_ndcg":
        return xgb.XGBRanker(objective="rank:ndcg", lambdarank_pair_method="topk", lambdarank_num_pair_per_sample=10,
                             **common_x, **p)
    if spec.name == "xgb_rank_pairwise":
        return xgb.XGBRanker(objective="rank:pairwise", **common_x, **p)
    if spec.name == "lgbm_lambdarank":
        return lgb.LGBMRanker(objective="lambdarank", random_state=SEED, subsample=0.9, subsample_freq=1,
                              colsample_bytree=0.9, verbose=-1, n_jobs=-1, **p)
    if spec.name == "lgbm_reg":
        return lgb.LGBMRegressor(random_state=SEED, subsample=0.9, subsample_freq=1, colsample_bytree=0.9,
                                 verbose=-1, n_jobs=-1, **p)
    if spec.name == "hgb_reg":
        return HistGradientBoostingRegressor(random_state=SEED, **p)
    if spec.name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(**p))
    raise ValueError(spec.name)


SPECS = [
    Spec("xgb_reg", {"max_depth": [3, 4], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300],
                     "min_child_weight": [5]}, "reg"),
    Spec("xgb_reg_mono", {"max_depth": [3, 4], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300],
                          "min_child_weight": [5]}, "reg"),
    Spec("xgb_rank_ndcg", {"max_depth": [3, 4], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300]}, "rank"),
    Spec("xgb_rank_pairwise", {"max_depth": [3, 4], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300]}, "rank"),
    Spec("lgbm_lambdarank", {"num_leaves": [7, 15], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300],
                             "min_child_samples": [10]}, "rank"),
    Spec("lgbm_reg", {"num_leaves": [7, 15], "learning_rate": [0.05, 0.1], "n_estimators": [150, 300],
                      "min_child_samples": [10]}, "reg"),
    Spec("hgb_reg", {"max_depth": [3, 4], "learning_rate": [0.05, 0.1], "max_iter": [150, 300]}, "reg"),
    Spec("ridge", {"alpha": [0.1, 1.0, 10.0]}, "reg"),
]


def fit_predict(spec: Spec, p: dict, Xtr, ytr, gtr, Xte) -> np.ndarray:
    m = make(spec, p)
    if spec.kind == "rank":
        lab = np.rint(ytr / TARGET_SCALE).astype(int)
        order = np.argsort(_qid(gtr), kind="stable")
        Xs, ls, gs = Xtr[order], lab[order], _qid(gtr)[order]
        if spec.name.startswith("lgbm"):
            m.fit(Xs, ls, group=np.bincount(gs)[np.unique(gs)])
        else:
            m.fit(Xs, ls, qid=gs)
    else:
        m.fit(Xtr, ytr)
    return m.predict(Xte)


def per_jd_ndcg(pred, groups, meta, labels, k=10) -> dict[str, float]:
    out = {}
    for jd in np.unique(groups):
        idx = np.where(groups == jd)[0]
        order = idx[np.argsort(-pred[idx], kind="stable")]
        out[str(jd)] = ndcg_at_k([meta[i][1] for i in order], labels[jd], k)
    return out


def per_jd_mrr(pred, groups, meta, labels) -> dict[str, float]:
    out = {}
    for jd in np.unique(groups):
        idx = np.where(groups == jd)[0]
        order = idx[np.argsort(-pred[idx], kind="stable")]
        out[str(jd)] = mrr([meta[i][1] for i in order], labels[jd])
    return out


def choose_params(spec, X, y, g, meta, labels) -> dict:
    keys = list(spec.grid)
    best, best_s = None, -1.0
    inner = GroupKFold(n_splits=3)
    for combo in itertools.product(*[spec.grid[k] for k in keys]):
        p = dict(zip(keys, combo, strict=True))
        sc = []
        for tr, va in inner.split(X, y, g):
            pred = fit_predict(spec, p, X[tr], y[tr], g[tr], X[va])
            sc.append(np.mean(list(per_jd_ndcg(pred, g[va], [meta[i] for i in va], labels).values())))
        if np.mean(sc) > best_s:
            best, best_s = p, float(np.mean(sc))
    return best


def nested_cv(spec, train: tuple, test: tuple | None, labels, feat: str) -> dict:
    """train/test = (X, y, groups, meta) built over the TRAIN JDs; test=None -> same data (in-dist)."""
    Xa, ya, ga, ma = train
    Xb, yb, gb, mb = test if test is not None else train
    Xa, Xb = transform(Xa, ga, feat), transform(Xb, gb, feat)
    scores: dict[str, list[float]] = {}
    mrrs: dict[str, list[float]] = {}
    chosen = []
    for rep in range(REPEATS):
        outer = GroupKFold(n_splits=6, shuffle=True, random_state=SEED + rep)
        for tr_idx, _ in outer.split(Xa, ya, ga):
            tr_jds = set(ga[tr_idx])
            te_mask = np.array([g not in tr_jds for g in gb])
            p = choose_params(spec, Xa[tr_idx], ya[tr_idx], ga[tr_idx], [ma[i] for i in tr_idx], labels)
            chosen.append(p)
            pred = fit_predict(spec, p, Xa[tr_idx], ya[tr_idx], ga[tr_idx], Xb[te_mask])
            te_meta = [mb[i] for i in np.where(te_mask)[0]]
            for jd, v in per_jd_ndcg(pred, gb[te_mask], te_meta, labels).items():
                scores.setdefault(f"{rep}:{jd}", []).append(v)
            for jd, v in per_jd_mrr(pred, gb[te_mask], te_meta, labels).items():
                mrrs.setdefault(f"{rep}:{jd}", []).append(v)
    flat = {k: v[0] for k, v in scores.items()}
    return {"ndcg@10": float(np.mean(list(flat.values()))), "std": float(np.std(list(flat.values()))),
            "mrr": float(np.mean([v[0] for v in mrrs.values()])), "per_jd": flat, "chosen": chosen}


def paired(a: dict, b: dict, n_boot: int = 2000) -> dict:
    keys = sorted(a)
    d = np.array([a[k] - b[k] for k in keys])
    rng = np.random.default_rng(SEED)
    boots = [rng.choice(d, len(d), replace=True).mean() for _ in range(n_boot)]
    return {"mean_diff": float(d.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "wins": int((d > 1e-9).sum()), "losses": int((d < -1e-9).sum()), "ties": int((np.abs(d) <= 1e-9).sum())}


# ------------------------------------------------------------------ data
def reembed(docs: list[CorpusDoc], emb) -> list[CorpusDoc]:
    return [CorpusDoc(d.doc_id, d.profile, d.chunks,
                      np.asarray(emb.embed_documents([c.embedding_text() for c in d.chunks]), dtype=np.float32),
                      d.truth) for d in docs]


def dataset(docs, emb, jds, labels):
    return build_dataset(ReferenceRetriever(docs, emb), jds, labels, top_k=TOP_K)  # absolute; transform() decides


def main() -> None:
    from data_rank.make_corpus import StubEmbedder as EmbA
    from docint.stubs import StubEmbedder as EmbB

    t0 = time.time()
    jds = load_jds()
    labels = load_labels()
    train_jds = [j for j in jds if j["split"] == "train"]
    test_jds = [j for j in jds if j["split"] == "test"]
    ingested_p = DATA_DIR / "corpus_ingested.jsonl"
    if not ingested_p.exists():
        raise SystemExit("run `python data_rank/reingest_corpus.py` first")
    clean = load_corpus(DATA_DIR / "corpus.jsonl")
    ing = load_corpus(ingested_p)
    ea, eb = EmbA(), EmbB()
    ing_a = dataset(ing, ea, train_jds, labels)
    ing_b = dataset(reembed(ing, eb), eb, train_jds, labels)
    clean_a = dataset(clean, ea, train_jds, labels)
    print(f"datasets built in {time.time() - t0:.0f}s ({len(ing_a[1])} rows / {len(train_jds)} train JDs)")

    report: dict = {"protocol": __doc__, "stage1_models": {}, "stage2_features": {}, "stage3_skew": {}}
    # ---- stage 1: model families, in-distribution, Ekant's features
    for spec in SPECS:
        t = time.time()
        r = nested_cv(spec, ing_a, None, labels, "abs")
        report["stage1_models"][spec.name] = r
        print(f"[stage1] {spec.name:<18} NDCG@10 {r['ndcg@10']:.4f} (std {r['std']:.3f})  MRR {r['mrr']:.3f}  {time.time() - t:.0f}s")
    base = report["stage1_models"]["xgb_reg"]["per_jd"]
    for name, r in report["stage1_models"].items():
        r["vs_xgb_reg"] = paired(r["per_jd"], base)
    ranked = sorted(report["stage1_models"], key=lambda n: -report["stage1_models"][n]["ndcg@10"])
    finalists = list(dict.fromkeys(["xgb_reg", *ranked[:2]]))

    # ---- stage 2: feature treatment, in-dist and embedder shift
    for name in finalists:
        spec = next(s for s in SPECS if s.name == name)
        for feat in ("abs", "rel", "nosim"):
            for scen, test in (("in-dist", None), ("emb-shift", ing_b)):
                r = nested_cv(spec, ing_a, test, labels, feat)
                report["stage2_features"][f"{name}|{feat}|{scen}"] = r
                print(f"[stage2] {name:<18} {feat:<6} {scen:<10} NDCG@10 {r['ndcg@10']:.4f}  MRR {r['mrr']:.3f}")

    # ---- stage 3: training-data skew (perfect profiles vs re-ingested), xgb_reg + best
    for name in finalists:
        spec = next(s for s in SPECS if s.name == name)
        for train_name, train in (("clean->ingested", clean_a), ("ingested->ingested", ing_a)):
            r = nested_cv(spec, train, ing_a, labels, "rel")
            report["stage3_skew"][f"{name}|{train_name}"] = r
            print(f"[stage3] {name:<18} {train_name:<20} NDCG@10 {r['ndcg@10']:.4f}  MRR {r['mrr']:.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(report, default=float))
    OUT.write_text(json.dumps(slim, indent=1), encoding="utf-8")
    print(f"wrote {OUT} in {time.time() - t0:.0f}s; held-out test JDs ({len(test_jds)}) untouched")


if __name__ == "__main__":
    main()
