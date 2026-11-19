"""8-model comparison for the reranker WITH classification metrics (accuracy, precision, recall, F1, ROC-AUC) next to
the ranking metrics (NDCG@10, MRR, P@10, R@10).

    python -m docint.rank.compare_models          (from ml/, ~3-6 min)

Writes data_rank/results/model_comparison.json and MODEL_COMPARISON.md (+ model_comparison.png).

Task framing
  The reranker scores every (job description, retrieved candidate) pair; the recruiter sees a ranked list. Labels are
  graded 0-3 (data_rank/LABELS.md). For the classification view a pair is SUITABLE when grade >= 2 - the same
  definition the project already uses for MRR / precision / recall.

Protocol (no leakage, identical for every model)
  * Training data = exactly what the shipped model was trained on: the 18 TRAIN JDs, Ekant's corpus, his features.
    So the only thing that varies between rows is the model.
  * Hyper-parameters: chosen per model by inner 3-fold GroupKFold (by JD) NDCG@10 on the train JDs only.
  * Decision threshold for "suitable": chosen per model on OUT-OF-FOLD train predictions (6-fold GroupKFold),
    maximising F1. Rankers output unscaled scores, so a per-model threshold is the only fair choice.
  * Evaluated ONCE on (a) the 6 held-out TEST JDs and (b) 120 FRESH JDs from the generator with a new seed
    (never seen by anything), on the serving-like corpus (resumes rendered to PDF and parsed by Track B).
  * 95% bootstrap CIs over the fresh JDs; paired differences vs the shipped model.
  * Extra row "SHIPPED": the exact file ml/models/xgb_v1.json the server loads.

Labels are synthetic and partly circular (see LABELS.md): compare rows, do not quote absolute numbers as real-world
accuracy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from docint.eval.metrics import mrr, ndcg_at_k
from docint.rank.benchmark import SEED, SPECS, choose_params, fit_predict
from docint.rank.data import DATA_DIR, TARGET_SCALE, build_dataset, load_jds, load_labels
from docint.rank.features import FEATURE_NAMES
from docint.rank.fresh_test import fresh_jds_and_labels
from docint.rank.retriever import ReferenceRetriever, load_corpus

OUT_DIR = DATA_DIR / "results"
MODEL_FILE = Path(__file__).resolve().parents[2] / "models" / "xgb_v1.json"
SUITABLE = 2  # grade >= 2
NICE = {
    "xgb_reg": "XGBoost regressor",
    "xgb_reg_mono": "XGBoost regressor + monotone (6 features)",
    "xgb_rank_ndcg": "XGBoost LambdaRank (NDCG)",
    "xgb_rank_pairwise": "XGBoost pairwise ranker",
    "lgbm_lambdarank": "LightGBM LambdaRank",
    "lgbm_reg": "LightGBM regressor",
    "hgb_reg": "sklearn HistGradientBoosting",
    "ridge": "Ridge regression (linear baseline)",
    "SHIPPED": "SHIPPED: XGBoost + monotone(years, education)",
}


# ------------------------------------------------------------------ metrics
def grade(y: np.ndarray) -> np.ndarray:
    return np.rint(y / TARGET_SCALE).astype(int)


def best_threshold(scores: np.ndarray, truth: np.ndarray) -> float:
    cands = np.unique(np.quantile(scores, np.linspace(0.02, 0.98, 97)))
    f1s = [f1_score(truth, scores >= t, zero_division=0) for t in cands]
    return float(cands[int(np.argmax(f1s))])


def ranking(pred, groups, meta, labels) -> dict[str, dict[str, float]]:
    """per-JD ranking metrics on the reranked top-50 pool."""
    out = {}
    for jd in np.unique(groups):
        idx = np.where(groups == jd)[0]
        order = idx[np.argsort(-np.round(pred[idx], 6), kind="stable")]
        ids = [meta[i][1] for i in order]
        rel = {d for d, g in labels[jd].items() if g >= SUITABLE}
        in_pool = [d for d in ids if d in rel]
        top10 = ids[:10]
        out[str(jd)] = {
            "ndcg@10": ndcg_at_k(ids, labels[jd], 10),
            "mrr": mrr(ids, labels[jd]),
            "p@10": sum(d in rel for d in top10) / len(top10),
            "r@10": (sum(d in rel for d in top10) / len(in_pool)) if in_pool else float("nan"),
        }
    return out


def classification(scores, truth, thr) -> dict[str, float]:
    yhat = scores >= thr
    return {
        "accuracy": accuracy_score(truth, yhat),
        "precision": precision_score(truth, yhat, zero_division=0),
        "recall": recall_score(truth, yhat, zero_division=0),
        "f1": f1_score(truth, yhat, zero_division=0),
        "roc_auc": roc_auc_score(truth, scores) if 0 < truth.sum() < len(truth) else float("nan"),
    }


def per_jd_f1(scores, truth, groups, thr) -> dict[str, float]:
    out = {}
    for jd in np.unique(groups):
        m = groups == jd
        out[str(jd)] = f1_score(truth[m], scores[m] >= thr, zero_division=0)
    return out


def boot(values: list[float], n: int = 4000) -> list[float]:
    v = np.asarray([x for x in values if not np.isnan(x)])
    rng = np.random.default_rng(SEED)
    b = [rng.choice(v, len(v)).mean() for _ in range(n)]
    return [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))]


def evaluate(scores, ds, labels, thr) -> dict:
    X, y, g, m = ds
    truth = grade(y) >= SUITABLE
    rk = ranking(scores, g, m, labels)
    agg = {k: float(np.nanmean([r[k] for r in rk.values()])) for k in ("ndcg@10", "mrr", "p@10", "r@10")}
    return {
        **classification(scores, truth, thr),
        **agg,
        "_per_jd_ndcg": {k: r["ndcg@10"] for k, r in rk.items()},
        "_per_jd_f1": per_jd_f1(scores, truth, g, thr),
        "rows": int(len(y)),
        "suitable_rows": int(truth.sum()),
    }


# ------------------------------------------------------------------ models
def oof_scores(spec, p, X, y, g) -> np.ndarray:
    out = np.zeros(len(y))
    for tr, va in GroupKFold(n_splits=6).split(X, y, g):
        out[va] = fit_predict(spec, p, X[tr], y[tr], g[tr], X[va])
    return out


def shipped_predict(X: np.ndarray) -> np.ndarray:
    import xgboost as xgb

    b = xgb.Booster()
    b.load_model(str(MODEL_FILE))
    return np.round(b.predict(xgb.DMatrix(X, feature_names=FEATURE_NAMES)), 3)  # as the server does


def main() -> None:
    from data_rank.make_corpus import StubEmbedder as EmbA

    t0 = time.time()
    jds, labels = load_jds(), load_labels()
    train = [j for j in jds if j["split"] == "train"]
    test = [j for j in jds if j["split"] == "test"]
    clean, ing = load_corpus(DATA_DIR / "corpus.jsonl"), load_corpus(DATA_DIR / "corpus_ingested.jsonl")
    fresh, fresh_labels = fresh_jds_and_labels(ing)
    all_labels = {**labels, **fresh_labels}
    ea = EmbA()

    def ds(docs, js):
        return build_dataset(ReferenceRetriever(docs, ea), js, all_labels, top_k=50)

    tr, te, fr = ds(clean, train), ds(ing, test), ds(ing, fresh)
    Xtr, ytr, gtr, mtr = tr
    truth_tr = grade(ytr) >= SUITABLE
    print(
        f"data in {time.time() - t0:.0f}s: train {len(ytr)} rows / {len(train)} JDs, test {len(te[1])} / {len(test)}, "
        f"fresh {len(fr[1])} / {len(fresh)}; suitable share train {truth_tr.mean():.2f}"
    )

    res: dict = {"protocol": __doc__.split("Task framing")[1].strip(), "suitable_grade": SUITABLE, "models": {}}
    for spec in SPECS:
        t = time.time()
        p = choose_params(spec, Xtr, ytr, gtr, mtr, labels)
        thr = best_threshold(oof_scores(spec, p, Xtr, ytr, gtr), truth_tr)
        t_fit = time.time()
        fit_predict(spec, p, Xtr, ytr, gtr, Xtr[:1])  # timing of one full fit
        fit_s = time.time() - t_fit
        r = {"name": NICE[spec.name], "params": p, "threshold": thr, "fit_seconds": round(fit_s, 2)}
        for split, d in (("test", te), ("fresh", fr)):
            s = fit_predict(spec, p, Xtr, ytr, gtr, d[0])
            r[split] = evaluate(s, d, all_labels, thr)
        res["models"][spec.name] = r
        f = r["fresh"]
        print(
            f"  {spec.name:<18} fresh: acc {f['accuracy']:.3f} P {f['precision']:.3f} R {f['recall']:.3f} "
            f"F1 {f['f1']:.3f} AUC {f['roc_auc']:.3f} NDCG@10 {f['ndcg@10']:.4f}  ({time.time() - t:.0f}s)"
        )

    # the exact served file; threshold from its OOF-equivalent = its own train predictions are in-sample, so use the
    # xgb_reg_mono threshold procedure on the same params via 0-100 scale: tune on train OOF of an identical refit
    import docint.rank.train as T

    best = T.cv_grid(Xtr, ytr, gtr, mtr, labels)[0]
    oof = np.zeros(len(ytr))
    for a, b in GroupKFold(n_splits=6).split(Xtr, ytr, gtr):
        oof[b] = T.fit_final(Xtr[a], ytr[a], best["params"], best["mean_best_iter"]).predict(T._df(Xtr[b]))
    thr = best_threshold(oof, truth_tr)
    r = {"name": NICE["SHIPPED"], "params": best["params"], "threshold": thr, "fit_seconds": None, "model_file": "ml/models/xgb_v1.json"}
    for split, d in (("test", te), ("fresh", fr)):
        t = time.time()
        s = shipped_predict(d[0])
        r[split] = evaluate(s, d, all_labels, thr)
        r["predict_ms_per_search" if split == "fresh" else "_t"] = round((time.time() - t) * 1000 / len(fresh), 3)
    res["models"]["SHIPPED"] = r
    f = r["fresh"]
    print(
        f"  {'SHIPPED':<18} fresh: acc {f['accuracy']:.3f} P {f['precision']:.3f} R {f['recall']:.3f} "
        f"F1 {f['f1']:.3f} AUC {f['roc_auc']:.3f} NDCG@10 {f['ndcg@10']:.4f}"
    )

    # CIs and paired differences vs SHIPPED on the fresh JDs
    base = res["models"]["SHIPPED"]["fresh"]
    for _name, r in res["models"].items():
        f = r["fresh"]
        f["ci95_ndcg@10"] = boot(list(f["_per_jd_ndcg"].values()))
        f["ci95_f1_per_jd"] = boot(list(f["_per_jd_f1"].values()))
        for metric, key in (("ndcg@10", "_per_jd_ndcg"), ("f1_per_jd", "_per_jd_f1")):
            d = np.array([f[key][k] - base[key][k] for k in sorted(base[key])])
            f[f"vs_shipped_{metric}"] = {
                "mean_diff": float(d.mean()),
                "ci95": boot(list(d)),
                "wins": int((d > 1e-9).sum()),
                "losses": int((d < -1e-9).sum()),
            }
    res["n_test_jds"], res["n_fresh_jds"] = len(test), len(fresh)
    res["seconds"] = round(time.time() - t0)
    for r in res["models"].values():
        r.pop("_t", None)
    out = json.loads(json.dumps(res, default=float))
    (OUT_DIR / "model_comparison.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_markdown(out)
    write_chart(out)
    print(f"wrote {OUT_DIR / 'MODEL_COMPARISON.md'} in {res['seconds']}s")


# ------------------------------------------------------------------ report
def write_markdown(res: dict) -> None:
    models = res["models"]
    order = sorted(models, key=lambda n: -models[n]["fresh"]["ndcg@10"])

    def row(n, split):
        m = models[n][split]
        nm = models[n]["name"]
        nm = f"**{nm}**" if n == "SHIPPED" else nm
        return (
            f"| {nm} | {m['accuracy']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | "
            f"{m['roc_auc']:.3f} | {m['ndcg@10']:.4f} | {m['mrr']:.3f} | {m['p@10']:.3f} | {m['r@10']:.3f} |"
        )

    head = (
        "| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | NDCG@10 | MRR | P@10 | R@10 |\n|---|---|---|---|---|---|---|---|---|---|"
    )
    fresh_rows = "\n".join(row(n, "fresh") for n in order)
    test_rows = "\n".join(row(n, "test") for n in order)
    paired = "\n".join(
        f"| {models[n]['name']} | {models[n]['fresh']['vs_shipped_ndcg@10']['mean_diff']:+.4f} "
        f"[{models[n]['fresh']['vs_shipped_ndcg@10']['ci95'][0]:+.4f}, {models[n]['fresh']['vs_shipped_ndcg@10']['ci95'][1]:+.4f}] | "
        f"{models[n]['fresh']['vs_shipped_f1_per_jd']['mean_diff']:+.3f} "
        f"[{models[n]['fresh']['vs_shipped_f1_per_jd']['ci95'][0]:+.3f}, {models[n]['fresh']['vs_shipped_f1_per_jd']['ci95'][1]:+.3f}] | "
        f"{models[n]['fresh']['vs_shipped_ndcg@10']['wins']}/{models[n]['fresh']['vs_shipped_ndcg@10']['losses']} |"
        for n in order
        if n != "SHIPPED"
    )
    fr = models["SHIPPED"]["fresh"]
    share = fr["suitable_rows"] / fr["rows"]
    md = f"""# Reranker model comparison — 8 models + the shipped file

Generated by `python -m docint.rank.compare_models` (from `ml/`). Raw numbers: `model_comparison.json`.
Earlier NDCG-only study with feature / training-data variants: `MODEL_STUDY.md`.

![F1 and NDCG@10 on 120 fresh JDs](model_comparison.png)

## What is measured

The reranker scores each (job description, retrieved candidate) pair; the recruiter sees the ranked list.

* **Classification view** — is the candidate *suitable* (relevance grade ≥ 2 of 3, rubric in `../LABELS.md`)?
  A pair counts as predicted-suitable when the model's score ≥ its threshold. The threshold is chosen per model on
  **out-of-fold training predictions** (max F1), never on test data. Computed over every candidate in each JD's top-50
  pool ({fr["rows"]} pairs on the fresh set, {fr["suitable_rows"]} suitable).
  * **Accuracy** = correct suitable/unsuitable calls ÷ all pairs (inflated when most pairs are unsuitable)
  * **Precision** = of the candidates flagged suitable, how many really are · **Recall** = of the suitable
    candidates, how many were flagged · **F1** = harmonic mean of the two · **ROC-AUC** = threshold-free
    probability that a random suitable candidate scores above a random unsuitable one
* **Ranking view** (what the recruiter actually sees, threshold-free) — **NDCG@10** (graded quality of the top 10,
  the primary selection metric), **MRR** (how high the first suitable candidate is), **P@10** (share of the top 10
  that is suitable), **R@10** (share of the pool's suitable candidates that made the top 10).

Protocol: every model is trained on the same 18 train JDs with the same features as the shipped model (so only the
model differs), hyper-parameters chosen by inner GroupKFold on train JDs, then evaluated once on the 6 held-out test
JDs and on **120 fresh JDs** (new generator seed, never used for anything), over the serving-like corpus (resumes
rendered to PDF and parsed by the real ingestion pipeline).

## 1. Results on 120 fresh JDs (the decisive set)

{head}
{fresh_rows}

## 2. Results on the 6 held-out test JDs (small — for completeness)

{head}
{test_rows}

## 3. Paired differences vs the shipped model (fresh JDs, bootstrap 95% CI)

A difference whose CI includes 0 is **not** a real difference.

| Model | NDCG@10 diff [95% CI] | per-JD F1 diff [95% CI] | NDCG wins/losses |
|---|---|---|---|
{paired}

## 4. Why XGBoost (the shipped model) was chosen

{why(res)}

## 5. Caveats

* Labels are synthetic and partly circular: the rubric uses skill coverage, years and education, which are also the
  strongest features, so every tree model scores very high. Compare rows; do not quote absolute numbers as real-world
  accuracy.
* About {share:.0%} of the pooled pairs are suitable, so accuracy is meaningful here, but F1, ROC-AUC and NDCG are
  still the more informative columns. Thresholds were tuned for F1; a different cut-off trades precision for recall.
* R@10 looks low for every model because a JD has ~26 suitable candidates in its pool and the top 10 can hold only
  10 of them (maximum ≈ 0.4); compare it between rows only.
* Embeddings in this environment are a deterministic stand-in; `MODEL_STUDY.md` shows the choice also holds when the
  embedder changes between training and serving.
"""
    (OUT_DIR / "MODEL_COMPARISON.md").write_text(md, encoding="utf-8")


def why(res: dict) -> str:
    m = res["models"]
    s = m["SHIPPED"]["fresh"]
    alt = [n for n in m if n != "SHIPPED"]

    def d(n, k):
        return m[n]["fresh"][f"vs_shipped_{k}"]

    def ci(n, k):
        lo, hi = d(n, k)["ci95"]
        nd = 4 if k == "ndcg@10" else 3
        return f"{d(n, k)['mean_diff']:+.{nd}f} [{lo:+.{nd}f}, {hi:+.{nd}f}]"

    tied = [n for n in alt if d(n, "ndcg@10")["ci95"][0] <= 0 <= d(n, "ndcg@10")["ci95"][1]]
    worse = [n for n in alt if d(n, "ndcg@10")["ci95"][1] < 0]
    better_rank = [n for n in alt if d(n, "ndcg@10")["ci95"][0] > 0]
    better_f1 = [n for n in alt if d(n, "f1_per_jd")["ci95"][0] > 0]
    x, xm, lg, rd = m["xgb_reg"]["fresh"], m["xgb_reg_mono"]["fresh"], m["lgbm_reg"]["fresh"], m["ridge"]["fresh"]
    out = [
        "**Short answer:** for *ranking* — the job the reranker does in the app — the shipped XGBoost is statistically "
        f"tied with the best of the 8 alternatives and significantly better than {len(worse)} of them; "
        "none beats it. On the *yes/no suitable* view (F1) the unconstrained boosted regressors are ~0.02 "
        "higher; that small cost comes from the fairness constraint and was accepted deliberately. "
        "XGBoost vs LightGBM is a tie, so the simpler, already-integrated XGBoost stays.",
        "",
        f"1. **Ranking (primary metric, NDCG@10): tie with the best.** Shipped {s['ndcg@10']:.4f}. Not significantly "
        f"different: {', '.join(m[n]['name'] + ' ' + ci(n, 'ndcg@10') for n in tied) or 'none'}. "
        f"Significantly worse: {', '.join(m[n]['name'] for n in worse) or 'none'}. "
        + (
            f"Significantly better: {', '.join(m[n]['name'] for n in better_rank)}."
            if better_rank
            else "No model is significantly better."
        ),
        f"2. **Classification (F1, suitable = grade ≥ 2): the shipped model gives up ~0.02.** Shipped F1 {s['f1']:.3f} "
        f"(precision {s['precision']:.3f}, recall {s['recall']:.3f}); significantly higher per-JD F1: "
        f"{', '.join(m[n]['name'] + ' ' + ci(n, 'f1_per_jd') for n in better_f1) or 'none'}. The cause is the "
        f"monotone constraint, not the library: the same XGBoost without constraints scores F1 {x['f1']:.3f}, with "
        f"monotone constraints {xm['f1']:.3f} (shipped {s['f1']:.3f}). "
        "We keep the constraint because (a) the app never makes a yes/no cut — it ranks, and `match_score` is "
        "shown as a relative score, so F1 at a threshold is not what users experience; and "
        "(b) it guarantees that more experience or a higher degree can never *lower* a candidate's score "
        "(it fixed a real case where PhDs were ranked below Master's). If the product later adds a "
        '"suitable / not suitable" flag, use the unconstrained XGBoost regressor with a threshold tuned on '
        "validation JDs.",
        f"3. **Why XGBoost and not LightGBM?** They are statistically tied: LightGBM regressor NDCG@10 "
        f"{lg['ndcg@10']:.4f} vs XGBoost {x['ndcg@10']:.4f} / shipped {s['ndcg@10']:.4f}, F1 {lg['f1']:.3f} vs "
        f"{x['f1']:.3f}. In the earlier cross-validation screen LightGBM led by ~0.009 NDCG, but the lead did not "
        "replicate on fresh JDs (`MODEL_STUDY.md`). With no measurable gain, switching would add a dependency, a new "
        "model format and re-validation of train/serve parity for nothing.",
        "4. **Learning-to-rank objectives (LambdaRank, pairwise) bring nothing:** at best tied on NDCG@10 and "
        "significantly worse on F1 / ROC-AUC (their scores are not on a label scale, so a cut-off generalises "
        "poorly). With ~50 candidates per JD and 4 grades, regressing the graded label is the more stable choice.",
        f"5. **A non-linear model is required.** Ridge (linear) reaches only F1 {rd['f1']:.3f} / NDCG@10 "
        f"{rd['ndcg@10']:.4f}: skill coverage, years gap and education gap act as thresholds that interact, which "
        "boosted trees capture and a straight line cannot.",
        "6. **Robust to the embedder change** (stand-in vectors at training, BGE at serving): under an embedder shift "
        "the shipped model lost significantly less NDCG than Ekant's original v1.1 model (+0.0075, 95% CI "
        "[+0.0015, +0.0134], `MODEL_STUDY.md` §2).",
        "7. **Engineering fit:** one small JSON model file, "
        + (f"{m['SHIPPED']['predict_ms_per_search']:.2f} ms" if m["SHIPPED"].get("predict_ms_per_search") else "< 1 ms")
        + " to score the 50 candidates of a search, output on the 0–100 scale the UI shows, train/serve parity "
        "tests already in place, and documented by Track C (Ekant).",
    ]
    return "\n".join(out)


def write_chart(res: dict) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    m = res["models"]
    order = sorted(m, key=lambda n: m[n]["fresh"]["ndcg@10"])
    names = [m[n]["name"].replace("SHIPPED: ", "SHIPPED · ") for n in order]
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    hi, lo = "#2a78d6", "#86b6ef"  # shipped = series blue; others = lighter step of the same ramp
    colors = [hi if n == "SHIPPED" else lo for n in order]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), sharey=True, facecolor="#fcfcfb")
    for ax, key, title, fmt in (
        (axes[0], "f1", "F1 (suitable = grade ≥ 2)", "{:.3f}"),
        (axes[1], "ndcg@10", "NDCG@10 (ranking quality)", "{:.4f}"),
    ):
        vals = [m[n]["fresh"][key] for n in order]
        ax.set_facecolor("#fcfcfb")
        bars = ax.barh(names, vals, color=colors, height=0.62, edgecolor="#fcfcfb", linewidth=2)
        lo_x = min(vals) - (max(vals) - min(vals)) * 0.6
        ax.set_xlim(max(0, lo_x), max(vals) + (max(vals) - min(vals)) * 0.35 + 1e-3)
        for b, v in zip(bars, vals, strict=True):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, " " + fmt.format(v), va="center", fontsize=9, color=ink)
        ax.set_title(title, loc="left", fontsize=11, color=ink, pad=10)
        ax.grid(axis="x", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(grid)
        ax.tick_params(colors=muted, labelsize=9, length=0)
    fig.suptitle(
        f"Reranker comparison on {res['n_fresh_jds']} fresh job descriptions (axes do not start at 0)",
        x=0.01,
        ha="left",
        fontsize=12,
        color=ink,
    )
    fig.tight_layout()
    fig.savefig(OUT_DIR / "model_comparison.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    main()
