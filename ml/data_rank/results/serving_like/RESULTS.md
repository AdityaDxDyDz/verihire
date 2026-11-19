# Evaluation results (Track C)

Embedder: `stub-hash-384`; corpus: 360 synthetic resumes; 24 JDs (6 held out).
**Labels are derived from synthetic ground truth, so these numbers are optimistic. Do not present them as real-world accuracy.**

**Held-out test JDs** (n = 6 JDs; mean, std of NDCG@10 across JDs)

| System | NDCG@5 | NDCG@10 (std) | MRR | Recall@20 | P@5 |
|---|---|---|---|---|---|
| E0 keyword (BM25) | 0.520 | 0.592 (0.152) | 0.708 | 0.514 | 0.567 |
| E1 vector only | 0.468 | 0.488 (0.198) | 0.625 | 0.417 | 0.567 |
| E2 hybrid (RRF) | 0.489 | 0.546 (0.155) | 0.694 | 0.549 | 0.533 |
| E3 hybrid + XGBoost | 0.965 | 0.963 (0.062) | 1.000 | 0.801 | 1.000 |

**6-fold GroupKFold CV over all JDs** (n = 24 JDs; mean, std of NDCG@10 across JDs)

| System | NDCG@5 | NDCG@10 (std) | MRR | Recall@20 | P@5 |
|---|---|---|---|---|---|
| E0 keyword (BM25) | 0.660 | 0.700 (0.164) | 0.885 | 0.575 | 0.775 |
| E1 vector only | 0.524 | 0.552 (0.190) | 0.837 | 0.468 | 0.700 |
| E2 hybrid (RRF) | 0.597 | 0.631 (0.166) | 0.854 | 0.554 | 0.758 |
| E3 hybrid + XGBoost | 0.963 | 0.976 (0.034) | 1.000 | 0.760 | 0.983 |

**Synonym test** (relevant = resumes that name frameworks, not the generic keyword; 24 JDs)

| System | NDCG@10 | Recall@20 | MRR |
|---|---|---|---|
| E0 keyword (BM25) | 0.430 | 0.642 | 0.587 |
| E2 hybrid (RRF) | 0.505 | 0.750 | 0.670 |

**Citation validity** (independent substring re-check; top-5 candidates x 6 test JDs)

| LLM behaviour | citations | valid before verification | valid after |
|---|---|---|---|
| simulated hallucination (30% corrupted) | 139 | 78.4% | 100.0% |
| rule-based stub (honest) | 139 | 100.0% | 100.0% |

**Prompt-injection test** (test double that obeys injected text; measures our verifier, not Claude)

- 17 injection resumes x 6 JDs = 102 pairs; pass rate (no skill status raised without evidence): **100.0%**
- Skills wrongly marked present if verification were OFF: 576; after verification: 0
- Injection resumes in the E3 top-5: 1 (undeserved: 0)

**Latency** (stub models): search+rerank 49 ms/query; skill-gap 18 ms.
