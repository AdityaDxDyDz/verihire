"""E5: ONE feature function used for training AND serving (parity is tested)."""
from __future__ import annotations

import json
from pathlib import Path

from docint.contracts import CandidateEvidence, JobSpec
from docint.rank.queries import tokenize
from docint.rank.skillmatch import canonicalize, expand_implied, find_skills

FEATURE_SCHEMA_VERSION = "1"
FEATURE_NAMES = [
    "rrf_sum_top3", "rrf_max", "n_chunks_hit", "best_vec_sim", "mean_vec_sim",
    "required_skill_coverage", "preferred_skill_coverage", "n_required_missing",
    "years_exp", "years_gap", "edu_level", "edu_gap", "title_match",
    "experience_section_hit", "skills_in_experience_ratio",
]


class FeatureSchemaError(RuntimeError):
    pass


def schema_dict() -> dict:
    return {"schema_version": FEATURE_SCHEMA_VERSION, "features": FEATURE_NAMES}


def write_schema(path: str | Path) -> None:
    Path(path).write_text(json.dumps(schema_dict(), indent=2), encoding="utf-8")


def check_schema(saved: dict) -> None:
    if saved.get("schema_version") != FEATURE_SCHEMA_VERSION or saved.get("features") != FEATURE_NAMES:
        raise FeatureSchemaError(
            f"model feature schema {saved.get('schema_version')} {saved.get('features')} "
            f"!= code schema {FEATURE_SCHEMA_VERSION} {FEATURE_NAMES}. Retrain the model.")


def _canon_list(skills: list[str]) -> list[str]:
    out = []
    for s in skills:
        c = canonicalize(s) or s.strip().lower()
        if c and c not in out:
            out.append(c)
    return out


def _coverage(wanted: list[str], have: set[str]) -> tuple[float, int]:
    if not wanted:
        return 0.0, 0
    hit = sum(1 for s in wanted if s in have)
    return hit / len(wanted), len(wanted) - hit


def compute_features(job: JobSpec, ev: CandidateEvidence) -> dict[str, float]:
    p = ev.profile
    req = _canon_list(job.required_skills)
    pref = _canon_list(job.preferred_skills)
    have = expand_implied({s.lower() for s in p.skills})
    req_cov, n_req_missing = _coverage(req, have)
    pref_cov, _ = _coverage(pref, have)

    sims = [c.vec_sim for c in ev.chunks if c.vec_sim is not None]
    jt = set(tokenize(job.title or ""))
    pt = set(tok for t in p.titles for tok in tokenize(t))
    title_match = len(jt & pt) / len(jt) if jt else 0.0

    # evidence quality: are the skills shown in real experience text (not only a coursework line)?
    exp_found: set[str] = set()
    for c in ev.chunks:
        if c.chunk.section == "experience":
            exp_found.update(find_skills(c.chunk.text_scrubbed))
    exp_have = expand_implied(exp_found)
    exp_hit = sum(1 for s in req if s in exp_have) / len(req) if req else 0.0
    prof_sk = [s.lower() for s in p.skills]
    in_exp_ratio = (sum(1 for s in prof_sk if s in exp_found) / len(prof_sk)) if prof_sk else 0.0

    min_years = job.min_years or 0.0
    min_edu = job.min_education or 0
    vals = {
        "rrf_sum_top3": ev.rrf_sum_top3, "rrf_max": ev.rrf_max, "n_chunks_hit": float(ev.n_chunks_hit),
        "best_vec_sim": max(sims) if sims else 0.0, "mean_vec_sim": (sum(sims) / len(sims)) if sims else 0.0,
        "required_skill_coverage": req_cov, "preferred_skill_coverage": pref_cov,
        "n_required_missing": float(n_req_missing),
        "years_exp": float(p.years_experience), "years_gap": float(p.years_experience) - min_years,
        "edu_level": float(p.education_level), "edu_gap": float(p.education_level) - min_edu,
        "title_match": title_match, "experience_section_hit": exp_hit,
        "skills_in_experience_ratio": in_exp_ratio,
    }
    assert list(vals) == FEATURE_NAMES
    return vals
