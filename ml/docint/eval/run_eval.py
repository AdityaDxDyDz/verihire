"""E11: one command reproduces every number.

    cd ml && PYTHONPATH=. python -m docint.eval.run_eval [--embedder stub|real]

Systems: E0 keyword-only (BM25), E1 vector-only, E2 hybrid (RRF), E3 hybrid + XGBoost (final).
Reported on (a) the 6 held-out test JDs and (b) 6-fold GroupKFold CV over all 24 JDs.
Also: synonym test (E0 vs E2), citation validity before/after verification, injection tests, latency.
Writes results/*.json and results/RESULTS.md. LABELS ARE SYNTHETIC: numbers are optimistic (see model card).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

from docint.eval.metrics import all_metrics
from docint.llm.client import StubLLM
from docint.llm.mutating import MutatingLLM
from docint.llm.skillgap import analyze_skill_gap
from docint.llm.skillgap_rules import ObedientLLM
from docint.rank.data import DATA_DIR, build_dataset, jd_to_spec, load_jds, load_labels
from docint.rank.features import FEATURE_NAMES
from docint.rank.reranker import MODELS_DIR, get_reranker
from docint.rank.retriever import ReferenceRetriever, load_corpus
from docint.rank.train import _df, fit_final
from docint.contracts import RetrievedChunk

SYSTEMS = ["E0", "E1", "E2", "E3"]
NAMES = {"E0": "E0 keyword (BM25)", "E1": "E1 vector only", "E2": "E2 hybrid (RRF)", "E3": "E3 hybrid + XGBoost"}
METRICS = ["ndcg@5", "ndcg@10", "mrr", "recall@20", "p@5"]
STATUS_RANK = {"missing": 0, "partial": 1, "present": 2}


def load_embedder(kind: str):
    if kind == "real":  # Harsh's BGE-small embedder (PR #1)
        from docint.embed import get_embedder
        return get_embedder()
    from data_rank.make_corpus import StubEmbedder
    return StubEmbedder()


def ranked_lists(retr, job, reranker, top_k=50) -> dict[str, list[str]]:
    hy = retr.search(job, "hybrid")
    rr = reranker.rerank(job, hy[:top_k])
    return {"E0": [c.candidate_id for c in retr.search(job, "bm25")],
            "E1": [c.candidate_id for c in retr.search(job, "vector")],
            "E2": [c.candidate_id for c in hy], "E3": [r.candidate_id for r in rr]}


def summarize(per_jd: dict[str, list[dict]]) -> dict:
    return {s: {m: {"mean": float(np.mean([r[m] for r in rows])), "std": float(np.std([r[m] for r in rows]))}
                for m in METRICS} | {"n_jds": len(rows)} for s, rows in per_jd.items()}


def md_table(summ: dict, title: str) -> str:
    n = next(iter(summ.values()))["n_jds"]
    lines = [f"**{title}** (n = {n} JDs; mean, std of NDCG@10 across JDs)", "",
             "| System | NDCG@5 | NDCG@10 (std) | MRR | Recall@20 | P@5 |", "|---|---|---|---|---|---|"]
    for s in SYSTEMS:
        m = summ[s]
        lines.append(f"| {NAMES[s]} | {m['ndcg@5']['mean']:.3f} | {m['ndcg@10']['mean']:.3f} ({m['ndcg@10']['std']:.3f}) | "
                     f"{m['mrr']['mean']:.3f} | {m['recall@20']['mean']:.3f} | {m['p@5']['mean']:.3f} |")
    return "\n".join(lines)


def cv_e3(retr, jds, labels, meta_best, n_splits=6, top_k=50) -> dict[str, dict[str, list[str]]]:
    """Fresh XGBoost per fold; hyper-parameters from the TRAIN-JD grid search (not re-tuned here)."""
    X, y, groups, meta = build_dataset(retr, jds, labels, top_k=top_k)
    out: dict[str, list[str]] = {}
    for tr, va in GroupKFold(n_splits=n_splits).split(X, y, groups):
        m = fit_final(X[tr], y[tr], meta_best["params"], meta_best["mean_best_iter"])
        pred = m.predict(_df(X[va]))
        for jd_id in sorted(set(groups[va])):
            idx = np.where(groups[va] == jd_id)[0]
            # v1.2.3: round to 0.001 so last-bit differences between XGBoost builds (Windows vs Linux) cannot
            # reorder near-ties; ties keep the stable retrieval order
            order = idx[np.argsort(-np.round(pred[idx], 3), kind="stable")]
            out[jd_id] = [meta[va[i]][1] for i in order]
    return out


def chunks_of(doc) -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk_id=f"{doc.doc_id}:{c.chunk_index}", chunk=c) for c in doc.chunks]


def citation_validity(docs_by_id, pairs, llm_factory) -> dict:
    """Independent re-check (plain `in`) of every citation before and after verification."""
    before_ok = before_n = after_ok = after_n = dropped = 0
    for i, (jd, doc_id) in enumerate(pairs):
        doc = docs_by_id[doc_id]
        job = jd_to_spec(jd)
        chs = chunks_of(doc)
        texts = {c.chunk_id: c.chunk.text_scrubbed for c in chs}
        llm = llm_factory(i)  # per-pair seed: a shared seed would corrupt the same positions every time
        spy = {}

        class Spy:
            model = llm.model

            def complete_json(self, **kw):
                spy["raw"] = llm.complete_json(**kw)
                return spy["raw"]
        res = analyze_skill_gap(job, doc.profile, chs, Spy())
        for s in spy["raw"]["skills"]:
            for c in s["citations"]:
                before_n += 1
                before_ok += int(c["chunk_id"] in texts and c["quote"] in texts[c["chunk_id"]])
        for a in res.skills:
            for c in a.citations:
                after_n += 1
                after_ok += int(c.chunk_id in texts and c.quote in texts[c.chunk_id])
        dropped += res.dropped_unverified_claims
    return {"citations_before": before_n, "valid_before": before_ok / max(before_n, 1),
            "citations_after": after_n, "valid_after": after_ok / max(after_n, 1), "dropped_claims": dropped}


def injection_test(docs, jds_test) -> dict:
    inj = [d for d in docs if d.truth.get("kind") == "injection"]
    passed = total = naive_upgrades = verified_upgrades = 0
    honest_llm, obedient = StubLLM(recorded_dir=None), ObedientLLM()
    for d in inj:
        for jd in jds_test:
            job, chs = jd_to_spec(jd), chunks_of(d)
            honest = {a.skill: a.status for a in analyze_skill_gap(job, d.profile, chs, honest_llm).skills}
            raw = obedient.complete_json(system="", user="", tool_name="emit_skill_gap", schema={},
                                         payload={"skills": list(honest), "chunks": [
                                             {"chunk_id": c.chunk_id, "section": c.chunk.section, "text": c.chunk.text_scrubbed} for c in chs]})
            naive_upgrades += sum(1 for s in raw["skills"] if STATUS_RANK["present"] > STATUS_RANK[honest[s["skill"]]])
            attacked = {a.skill: a.status for a in analyze_skill_gap(job, d.profile, chs, obedient).skills}
            up = sum(1 for s, st in attacked.items() if STATUS_RANK[st] > STATUS_RANK[honest[s]])
            verified_upgrades += up
            total += 1
            passed += int(up == 0)
    return {"injection_resumes": len(inj), "pairs_tested": total, "pass_rate": passed / max(total, 1),
            "skills_wrongly_upgraded_without_verification": naive_upgrades,
            "skills_wrongly_upgraded_after_verification": verified_upgrades}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default="stub", choices=["stub", "real"])
    ap.add_argument("--corpus", default=str(DATA_DIR / "corpus.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "results"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    docs = load_corpus(a.corpus)
    docs_by_id = {d.doc_id: d for d in docs}
    jds = load_jds()
    labels = load_labels()
    meta = json.loads((MODELS_DIR / "xgb_v1.meta.json").read_text(encoding="utf-8"))
    test_jds = [j for j in jds if j["split"] == "test"]
    # ---- leakage guards (also asserted in tests) ----
    assert not set(meta["train_jd_ids"]) & {j["jd_id"] for j in test_jds}, "LEAK: test JD in training set"
    assert {j["jd_id"] for j in test_jds} == set(meta["test_jd_ids"])
    emb = load_embedder(a.embedder)
    retr = ReferenceRetriever(docs, emb)
    reranker = get_reranker()
    assert not reranker.is_fallback, "xgb_v1.json missing: run `python -m docint.rank.train` first"

    # ---- (a) held-out test JDs ----
    per_test: dict[str, list[dict]] = {s: [] for s in SYSTEMS}
    lat = []
    for jd in test_jds:
        t0 = time.perf_counter()
        ranked = ranked_lists(retr, jd_to_spec(jd), reranker)
        lat.append((time.perf_counter() - t0) * 1000)
        for s in SYSTEMS:
            per_test[s].append(all_metrics(ranked[s], labels[jd["jd_id"]]))
    test_summary = summarize(per_test)

    # ---- (b) CV over all JDs ----
    cv_ranked = cv_e3(retr, jds, labels, meta["best"])
    per_cv: dict[str, list[dict]] = {s: [] for s in SYSTEMS}
    for jd in jds:
        base = ranked_lists(retr, jd_to_spec(jd), reranker)  # E0-E2 need no training
        base["E3"] = cv_ranked[jd["jd_id"]]
        for s in SYSTEMS:
            per_cv[s].append(all_metrics(base[s], labels[jd["jd_id"]]))
    cv_summary = summarize(per_cv)

    # ---- synonym test: only resumes written with framework names (no literal generic keyword) ----
    syn_ids = {d.doc_id for d in docs if d.truth.get("kind") == "synonym"}
    syn_rows: dict[str, list[dict]] = {"E0": [], "E2": []}
    for jd in jds:
        rel = {d: (l if d in syn_ids else 0) for d, l in labels[jd["jd_id"]].items()}
        if sum(1 for v in rel.values() if v >= 2) == 0:
            continue
        ranked = ranked_lists(retr, jd_to_spec(jd), reranker)
        for s in syn_rows:
            syn_rows[s].append(all_metrics(ranked[s], rel))
    synonym = {s: {m: float(np.mean([r[m] for r in rows])) for m in METRICS} | {"n_jds": len(rows)}
               for s, rows in syn_rows.items()}

    # ---- citation validity before / after verification (simulated hallucination) + injection ----
    top_pairs = []
    for jd in test_jds:
        ranked = ranked_lists(retr, jd_to_spec(jd), reranker)["E3"][:5]
        top_pairs += [(jd, d) for d in ranked]
    cite_halluc = citation_validity(docs_by_id, top_pairs, lambda i: MutatingLLM(StubLLM(recorded_dir=None), rate=0.3, seed=1000 + i))
    cite_clean = citation_validity(docs_by_id, top_pairs, lambda i: StubLLM(recorded_dir=None))
    inj = injection_test(docs, test_jds)
    # do injection resumes sneak into the top 5 of the final ranking without deserving it (label < 2)?
    inj_ids = {d.doc_id for d in docs if d.truth.get("kind") == "injection"}
    bad_top5 = tot_top5 = 0
    for jd in test_jds:
        for d in ranked_lists(retr, jd_to_spec(jd), reranker)["E3"][:5]:
            if d in inj_ids:
                tot_top5 += 1
                bad_top5 += int(labels[jd["jd_id"]][d] < 2)
    inj["injection_resumes_in_top5_total"] = tot_top5
    inj["injection_resumes_in_top5_undeserved"] = bad_top5

    # ---- latency (stub LLM / stub embedder; real models will be slower) ----
    t0 = time.perf_counter()
    d0 = docs_by_id[ranked_lists(retr, jd_to_spec(test_jds[0]), reranker)["E3"][0]]
    analyze_skill_gap(jd_to_spec(test_jds[0]), d0.profile, chunks_of(d0), StubLLM(recorded_dir=None))
    latency = {"search_rerank_ms_mean": float(np.mean(lat)), "skill_gap_stub_ms": (time.perf_counter() - t0) * 1000,
               "note": "stub embedder/LLM; re-measure with real models (target: search < 3 s, analysis < 15 s)"}

    results = {"embedder": emb.name, "n_docs": len(docs), "n_jds": len(jds), "test_jd_ids": [j["jd_id"] for j in test_jds],
               "test": test_summary, "cv": cv_summary, "synonym_test": synonym,
               "citation_validity_simulated_hallucination": cite_halluc, "citation_validity_stub_llm": cite_clean,
               "injection": inj, "latency": latency}
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    md = ["# Evaluation results (Track C)", "",
          f"Embedder: `{emb.name}`; corpus: {len(docs)} synthetic resumes; {len(jds)} JDs ({len(test_jds)} held out).",
          "**Labels are derived from synthetic ground truth, so these numbers are optimistic. Do not present them as real-world accuracy.**",
          "", md_table(test_summary, "Held-out test JDs"), "", md_table(cv_summary, "6-fold GroupKFold CV over all JDs"), "",
          "**Synonym test** (relevant = resumes that name frameworks, not the generic keyword; "
          f"{synonym['E0']['n_jds']} JDs)", "", "| System | NDCG@10 | Recall@20 | MRR |", "|---|---|---|---|"]
    for s in ("E0", "E2"):
        md.append(f"| {NAMES[s]} | {synonym[s]['ndcg@10']:.3f} | {synonym[s]['recall@20']:.3f} | {synonym[s]['mrr']:.3f} |")
    md += ["", "**Citation validity** (independent substring re-check; top-5 candidates x 6 test JDs)", "",
           "| LLM behaviour | citations | valid before verification | valid after |", "|---|---|---|---|",
           f"| simulated hallucination (30% corrupted) | {cite_halluc['citations_before']} | {cite_halluc['valid_before']:.1%} | {cite_halluc['valid_after']:.1%} |",
           f"| rule-based stub (honest) | {cite_clean['citations_before']} | {cite_clean['valid_before']:.1%} | {cite_clean['valid_after']:.1%} |",
           "", "**Prompt-injection test** (test double that obeys injected text; measures our verifier, not Claude)", "",
           f"- {inj['injection_resumes']} injection resumes x {len(test_jds)} JDs = {inj['pairs_tested']} pairs; pass rate (no skill status raised without evidence): **{inj['pass_rate']:.1%}**",
           f"- Skills wrongly marked present if verification were OFF: {inj['skills_wrongly_upgraded_without_verification']}; after verification: {inj['skills_wrongly_upgraded_after_verification']}",
           f"- Injection resumes in the E3 top-5: {inj['injection_resumes_in_top5_total']} (undeserved: {inj['injection_resumes_in_top5_undeserved']})",
           "", f"**Latency** (stub models): search+rerank {latency['search_rerank_ms_mean']:.0f} ms/query; skill-gap {latency['skill_gap_stub_ms']:.0f} ms."]
    (out / "RESULTS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
