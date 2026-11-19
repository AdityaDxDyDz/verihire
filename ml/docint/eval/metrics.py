"""Ranking metrics. `rel` maps doc_id -> graded label 0-3 for the WHOLE corpus (not only the
retrieved docs), so a retriever that never surfaces a good resume is penalised in
NDCG and Recall."""
from __future__ import annotations

import math

REL_THRESHOLD = 2  # label >= 2 counts as "relevant" for MRR / Recall / Precision


def _dcg(gains: list[float], k: int) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(ranked: list[str], rel: dict[str, int], k: int) -> float:
    ideal = sorted(rel.values(), reverse=True)
    idcg = _dcg(ideal, k)
    if idcg == 0:
        return 0.0
    return _dcg([rel.get(d, 0) for d in ranked], k) / idcg


def mrr(ranked: list[str], rel: dict[str, int], thr: int = REL_THRESHOLD) -> float:
    for i, d in enumerate(ranked, start=1):
        if rel.get(d, 0) >= thr:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked: list[str], rel: dict[str, int], k: int, thr: int = REL_THRESHOLD) -> float:
    total = sum(1 for v in rel.values() if v >= thr)
    if total == 0:
        return 0.0
    return sum(1 for d in ranked[:k] if rel.get(d, 0) >= thr) / total


def precision_at_k(ranked: list[str], rel: dict[str, int], k: int, thr: int = REL_THRESHOLD) -> float:
    if k == 0:
        return 0.0
    return sum(1 for d in ranked[:k] if rel.get(d, 0) >= thr) / k


def all_metrics(ranked: list[str], rel: dict[str, int]) -> dict[str, float]:
    return {"ndcg@5": ndcg_at_k(ranked, rel, 5), "ndcg@10": ndcg_at_k(ranked, rel, 10),
            "mrr": mrr(ranked, rel), "recall@20": recall_at_k(ranked, rel, 20),
            "p@5": precision_at_k(ranked, rel, 5)}
