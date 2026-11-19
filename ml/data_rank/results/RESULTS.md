# Evaluation results (Track C)

Embedder: `stub-hash-384`; corpus: 360 synthetic resumes; 24 JDs (6 held out).
**Labels are derived from synthetic ground truth, so these numbers are optimistic. Do not present them as real-world accuracy.**

**Held-out test JDs** (n = 6 JDs; mean, std of NDCG@10 across JDs)

| System | NDCG@5 | NDCG@10 (std) | MRR | Recall@20 | P@5 |
|---|---|---|---|---|---|
| E0 keyword (BM25) | 0.602 | 0.608 (0.156) | 0.792 | 0.535 | 0.667 |
| E1 vector only | 0.455 | 0.478 (0.209) | 0.617 | 0.417 | 0.567 |
| E2 hybrid (RRF) | 0.511 | 0.567 (0.168) | 0.806 | 0.556 | 0.533 |
| E3 hybrid + XGBoost | 0.952 | 0.957 (0.073) | 1.000 | 0.801 | 1.000 |

**6-fold GroupKFold CV over all JDs** (n = 24 JDs; mean, std of NDCG@10 across JDs)

| System | NDCG@5 | NDCG@10 (std) | MRR | Recall@20 | P@5 |
|---|---|---|---|---|---|
| E0 keyword (BM25) | 0.690 | 0.703 (0.169) | 0.906 | 0.577 | 0.808 |
| E1 vector only | 0.518 | 0.549 (0.193) | 0.856 | 0.468 | 0.692 |
| E2 hybrid (RRF) | 0.607 | 0.638 (0.168) | 0.882 | 0.558 | 0.758 |
| E3 hybrid + XGBoost | 0.974 | 0.978 (0.034) | 1.000 | 0.762 | 0.983 |

**Synonym test** (relevant = resumes that name frameworks, not the generic keyword; 24 JDs)

| System | NDCG@10 | Recall@20 | MRR |
|---|---|---|---|
| E0 keyword (BM25) | 0.435 | 0.643 | 0.619 |
| E2 hybrid (RRF) | 0.503 | 0.770 | 0.657 |

**Citation validity** (independent substring re-check; top-5 candidates x 6 test JDs)

| LLM behaviour | citations | valid before verification | valid after |
|---|---|---|---|
| simulated hallucination (30% corrupted) | 137 | 78.1% | 100.0% |
| rule-based stub (honest) | 137 | 100.0% | 100.0% |

**Prompt-injection test** (test double that obeys injected text; measures our verifier, not Claude)

- 17 injection resumes x 6 JDs = 102 pairs; pass rate (no skill status raised without evidence): **100.0%**
- Skills wrongly marked present if verification were OFF: 576; after verification: 0
- Injection resumes in the E3 top-5: 1 (undeserved: 0)

**Latency** (stub models): search+rerank 68 ms/query; skill-gap 19 ms.
