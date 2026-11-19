"""Loaders for ml/data_rank files + training-set builder."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from docint.contracts import JobSpec
from docint.rank.features import FEATURE_NAMES, compute_features

DATA_DIR = Path(__file__).resolve().parents[2] / "data_rank"
TARGET_SCALE = 100.0 / 3.0  # target = label / 3 * 100


def load_jds(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else DATA_DIR / "jds.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_labels(path: str | Path | None = None) -> dict[str, dict[str, int]]:
    """jd_id -> {doc_id: label} over the WHOLE corpus."""
    p = Path(path) if path else DATA_DIR / "labels.jsonl"
    out: dict[str, dict[str, int]] = {}
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            out.setdefault(r["jd_id"], {})[r["doc_id"]] = r["label"]
    return out


def jd_to_spec(jd: dict) -> JobSpec:
    """Gold JobSpec built from the JD generator's own fields (what a perfect parser returns)."""
    return JobSpec(title=jd["title"], raw_text=jd["text"], required_skills=jd["required"],
                   preferred_skills=jd["preferred"], min_years=jd["min_years"] or None,
                   min_education=jd["min_education"],
                   responsibilities=["Design, build and ship reliable features", "Collaborate with cross-functional teams"])


def build_dataset(retriever, jds: list[dict], labels: dict, top_k: int = 50, mode: str = "hybrid"):
    """Rows = (JD, retrieved candidate). Returns X (n,F), y (n,) on a 0-100 scale, groups (jd ids), meta."""
    X, y, groups, meta = [], [], [], []
    for jd in jds:
        job = jd_to_spec(jd)
        for ev in retriever.search(job, mode)[:top_k]:
            f = compute_features(job, ev)
            X.append([f[n] for n in FEATURE_NAMES])
            y.append(labels[jd["jd_id"]][ev.candidate_id] * TARGET_SCALE)
            groups.append(jd["jd_id"])
            meta.append((jd["jd_id"], ev.candidate_id))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), np.asarray(groups), meta
