"""E6 serving side: get_reranker() -> reranker with .rerank(job, candidates) and .rank() (contract alias, v1.2).

- XGBReranker: loads ml/models/xgb_v1.json; match_score in 0-100 (a RELATIVE ranking score,
  not a probability); feature_contributions from xgboost pred_contribs (per-feature, same units
  as match_score; bias kept in `.last_bias`).
- RRFReranker: used when the model file is missing -> retrieval order (reported via is_fallback).
- A model trained with a different feature schema fails loudly (FeatureSchemaError).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import xgboost as xgb

from docint.contracts import CandidateEvidence, JobSpec, RankedCandidate
from docint.rank.features import FEATURE_NAMES, check_schema, compute_features

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
RERANKER_VERSION = "xgb_v1"


class RRFReranker:
    is_fallback = True
    version = "rrf_fallback"

    def rank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]:
        """Contract name (plan 7.2 / backend); same as rerank()."""
        return self.rerank(job, candidates)

    def rerank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]:
        top = max((c.rrf_sum_top3 for c in candidates), default=0.0) or 1.0
        ordered = sorted(candidates, key=lambda c: (-c.rrf_sum_top3, c.candidate_id))
        out = []
        for i, c in enumerate(ordered, start=1):
            f = compute_features(job, c)
            out.append(RankedCandidate(candidate_id=c.candidate_id, match_score=round(100 * c.rrf_sum_top3 / top, 2),
                                       rank=i, feature_values=f, feature_contributions={}))
        return out


class XGBReranker:
    is_fallback = False

    def __init__(self, model_path: Path, schema_path: Path):
        check_schema(json.loads(schema_path.read_text(encoding="utf-8")))
        self.booster = xgb.Booster()
        self.booster.load_model(str(model_path))
        meta_path = model_path.with_suffix(".meta.json")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        self.version = self.meta.get("version", RERANKER_VERSION)
        self.last_bias = 0.0

    def rank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]:
        """Contract name (plan 7.2 / backend); same as rerank()."""
        return self.rerank(job, candidates)

    def features_matrix(self, job: JobSpec, candidates: list[CandidateEvidence]) -> tuple[np.ndarray, list[dict]]:
        feats = [compute_features(job, c) for c in candidates]
        return np.asarray([[f[n] for n in FEATURE_NAMES] for f in feats], dtype=float), feats

    def rerank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]:
        if not candidates:
            return []
        X, feats = self.features_matrix(job, candidates)
        dm = xgb.DMatrix(X, feature_names=FEATURE_NAMES)
        raw = self.booster.predict(dm)
        contribs = self.booster.predict(dm, pred_contribs=True)  # (n, F+1), last col = bias
        self.last_bias = float(contribs[0, -1])
        # rounded to 0.001: identical ranking on every OS (XGBoost builds differ in the last bits); ties -> retrieval, id
        order = sorted(range(len(candidates)),
                       key=lambda i: (-round(float(raw[i]), 3), -candidates[i].rrf_sum_top3, candidates[i].candidate_id))
        out = []
        for rank, i in enumerate(order, start=1):
            out.append(RankedCandidate(
                candidate_id=candidates[i].candidate_id, match_score=float(np.clip(raw[i], 0, 100)), rank=rank,
                feature_values=feats[i],
                feature_contributions={n: float(contribs[i, j]) for j, n in enumerate(FEATURE_NAMES)}))
        return out


def get_reranker(models_dir: str | Path | None = None):
    d = Path(models_dir) if models_dir else MODELS_DIR
    model, schema = d / "xgb_v1.json", d / "feature_schema.json"
    if not model.exists() or not schema.exists():
        return RRFReranker()  # fall back to RRF order; backend adds a warnings[] entry
    return XGBReranker(model, schema)
