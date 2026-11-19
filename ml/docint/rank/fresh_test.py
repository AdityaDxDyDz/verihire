"""Decisive model comparison on a LARGE, FRESH test set (integration step, v1.2).

python -m docint.rank.fresh_test

Why: the 6 held-out JDs are too few to separate rankers (std of NDCG@10 across JDs ~0.1). Because the data is
synthetic, we can draw NEW JDs from Ekant's own generator (different seed, ids jdx_*) and label every corpus
resume from ground truth with his rubric. These JDs are never used for training, tuning or model choice before
this script runs, so the comparison is unbiased and has 120 queries instead of 6.

Compared (all evaluated on the serving-like re-ingested corpus):
  A  Ekant's original setup: XGBoost regressor, absolute features, trained on the perfect-profile corpus,
     his tuned parameters (from the v1.1 xgb_v1.meta.json, copied below)
  B  XGBoost regressor, relative features, trained on the re-ingested corpus (grid-tuned, train JDs only)
  C  LightGBM LambdaRank, relative features, trained on the re-ingested corpus (grid-tuned, train JDs only)
Scenarios: same embedder as training; embedder shift (a different embedding model at serving time); and
  bge-like range: the same vectors but cosine similarities compressed into 0.55-0.95 (sim' = 0.55 + 0.4*sim),
  the typical range of BGE-small, while training used the stub's wide range. Ranks do not change, values do.
  D  XGBoost regressor, min-max-per-search features (scale+offset invariant), re-ingested corpus
  E  LightGBM regressor, Ekant's features, generator corpus (best model of the CV screen on the final data)
  S  SHIPPED: Ekant's XGBoost regressor + monotone constraints on years/education, trained with the real
     docint.rank.train pipeline (its own CV grid) on the corpus - i.e. exactly what ml/models/xgb_v1.json is
Writes data_rank/results/fresh_test.json and prints paired bootstrap 95% CIs.
"""
from __future__ import annotations

import json
import random

import numpy as np

from docint.eval.metrics import mrr, ndcg_at_k, precision_at_k
from docint.rank.benchmark import SPECS, choose_params, fit_predict, reembed, transform
from docint.rank.data import DATA_DIR, build_dataset, load_jds, load_labels
from docint.rank.retriever import ReferenceRetriever, load_corpus

N_FRESH, FRESH_SEED = 120, 20261119
EKANT_V11 = {"max_depth": 4, "learning_rate": 0.1, "min_child_weight": 5, "n_estimators": 62}  # xgb_v1.meta.json (mean_best_iter)
OUT = DATA_DIR / "results" / "fresh_test.json"


def fresh_jds_and_labels(docs) -> tuple[list[dict], dict]:
    from data_rank.make_corpus import FAMILY_NAMES, graded_label, make_jd
    rng = random.Random(FRESH_SEED)
    jds = []
    for i in range(N_FRESH):
        jd = make_jd(1000 + i, rng, FAMILY_NAMES[i % len(FAMILY_NAMES)])
        jd["jd_id"] = f"jdx_{i:03d}"
        jds.append(jd)
    labels = {jd["jd_id"]: {d.doc_id: graded_label(jd, d.truth) for d in docs} for jd in jds}
    return jds, labels


def evaluate(pred, groups, meta, labels) -> dict[str, dict[str, float]]:
    out = {}
    for jd in np.unique(groups):
        idx = np.where(groups == jd)[0]
        ranked = [meta[i][1] for i in idx[np.argsort(-pred[idx], kind="stable")]]
        out[str(jd)] = {"ndcg@10": ndcg_at_k(ranked, labels[jd], 10), "mrr": mrr(ranked, labels[jd]),
                        "p@5": precision_at_k(ranked, labels[jd], 5)}
    return out


def boot_ci(d: np.ndarray, n: int = 5000) -> tuple[float, float]:
    rng = np.random.default_rng(7)
    m = [rng.choice(d, len(d), replace=True).mean() for _ in range(n)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def main() -> None:
    from data_rank.make_corpus import StubEmbedder as EmbA
    from docint.stubs import StubEmbedder as EmbB

    jds, labels = load_jds(), load_labels()
    train = [j for j in jds if j["split"] == "train"]
    clean, ing = load_corpus(DATA_DIR / "corpus.jsonl"), load_corpus(DATA_DIR / "corpus_ingested.jsonl")
    ea, eb = EmbA(), EmbB()
    fresh, fresh_labels = fresh_jds_and_labels(ing)
    all_labels = {**labels, **fresh_labels}

    def ds(docs, emb, js):
        return build_dataset(ReferenceRetriever(docs, emb), js, all_labels, top_k=50)

    spec = {s.name: s for s in SPECS}
    tr_clean, tr_ing = ds(clean, ea, train), ds(ing, ea, train)
    te = {"same embedder": ds(ing, ea, fresh), "embedder shift": ds(reembed(ing, eb), eb, fresh)}
    from docint.rank.features import FEATURE_NAMES
    Xs, ys, gs, ms = te["same embedder"]
    Xc = Xs.copy()
    for name in ("best_vec_sim", "mean_vec_sim"):
        j = FEATURE_NAMES.index(name)
        Xc[:, j] = 0.55 + 0.4 * Xc[:, j]
    te["bge-like range"] = (Xc, ys, gs, ms)

    def tuned(model_type, feat="rel"):  # grid on TRAIN JDs only (inner GroupKFold, as in the benchmark)
        X, y, g, m = tr_ing
        return choose_params(spec[model_type], transform(X, g, feat), y, g, m, labels)

    configs = {"A_ekant_original": ("xgb_reg", EKANT_V11, tr_clean, "abs"),
               "B_xgb_relative": ("xgb_reg", tuned("xgb_reg"), tr_ing, "rel"),
               "C_lgbm_lambdarank_relative": ("lgbm_lambdarank", tuned("lgbm_lambdarank"), tr_ing, "rel"),
               "D_xgb_minmax": ("xgb_reg", tuned("xgb_reg", "minmax"), tr_ing, "minmax"),
               "E_lgbm_reg": ("lgbm_reg", choose_params(spec["lgbm_reg"], *tr_clean, labels), tr_clean, "abs")}
    import docint.rank.train as T
    best = T.cv_grid(*tr_clean, labels)[0]
    shipped = T.fit_final(tr_clean[0], tr_clean[1], best["params"], best["mean_best_iter"])
    res: dict = {"n_fresh_jds": N_FRESH, "seed": FRESH_SEED, "configs": {}, "paired": {}}
    for name, (mt, params, (X, y, g, _), feat) in configs.items():
        res["configs"][name] = {"model": mt, "params": params, "features": feat}
        for scen, (Xt, _, gt, mt_) in te.items():
            pred = fit_predict(spec[mt], params, transform(X, g, feat), y, g, transform(Xt, gt, feat))
            per = evaluate(pred, gt, mt_, all_labels)
            res["configs"][name][scen] = {
                "per_jd": per, **{k: float(np.mean([v[k] for v in per.values()])) for k in ("ndcg@10", "mrr", "p@5")},
                "ndcg_std": float(np.std([v["ndcg@10"] for v in per.values()]))}
    res["configs"]["S_shipped"] = {"model": "xgb_reg + monotone(years, education)", "params": best["params"],
                                   "n_estimators": best["mean_best_iter"], "features": "abs"}
    for scen, (Xt, _, gt, mt_) in te.items():
        per = evaluate(T.model_predict(shipped, Xt) if hasattr(T, "model_predict") else shipped.predict(T._df(Xt)),
                       gt, mt_, all_labels)
        res["configs"]["S_shipped"][scen] = {
            "per_jd": per, **{k: float(np.mean([v[k] for v in per.values()])) for k in ("ndcg@10", "mrr", "p@5")},
            "ndcg_std": float(np.std([v["ndcg@10"] for v in per.values()]))}
    for scen in te:
        base = res["configs"]["A_ekant_original"][scen]["per_jd"]
        for name in ("B_xgb_relative", "C_lgbm_lambdarank_relative", "D_xgb_minmax", "E_lgbm_reg", "S_shipped"):
            cur = res["configs"][name][scen]["per_jd"]
            d = np.array([cur[k]["ndcg@10"] - base[k]["ndcg@10"] for k in sorted(base)])
            res["paired"][f"{name} vs A | {scen}"] = {"mean_diff": float(d.mean()), "ci95": boot_ci(d),
                                                      "wins": int((d > 1e-9).sum()), "losses": int((d < -1e-9).sum())}
        b = res["configs"]["B_xgb_relative"][scen]["per_jd"]
        for other in ("C_lgbm_lambdarank_relative", "D_xgb_minmax", "E_lgbm_reg", "S_shipped"):
            c = res["configs"][other][scen]["per_jd"]
            d = np.array([c[k]["ndcg@10"] - b[k]["ndcg@10"] for k in sorted(b)])
            res["paired"][f"{other} vs B | {scen}"] = {"mean_diff": float(d.mean()), "ci95": boot_ci(d),
                                                       "wins": int((d > 1e-9).sum()), "losses": int((d < -1e-9).sum())}
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    for name, c in res["configs"].items():
        for scen in te:
            r = c[scen]
            print(f"{name:<28} {scen:<15} NDCG@10 {r['ndcg@10']:.4f} (std {r['ndcg_std']:.3f})  MRR {r['mrr']:.3f}  P@5 {r['p@5']:.3f}")
    for k, v in res["paired"].items():
        print(f"{k:<52} diff {v['mean_diff']:+.4f}  95% CI [{v['ci95'][0]:+.4f}, {v['ci95'][1]:+.4f}]  W/L {v['wins']}/{v['losses']}")


if __name__ == "__main__":
    main()
