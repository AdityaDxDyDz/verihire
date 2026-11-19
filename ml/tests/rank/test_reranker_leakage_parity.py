import json
import shutil

import numpy as np
import pytest
import xgboost as xgb

from docint.rank.data import DATA_DIR, build_dataset, jd_to_spec, load_jds, load_labels
from docint.rank.features import FEATURE_NAMES, FeatureSchemaError, check_schema, compute_features
from docint.rank.reranker import MODELS_DIR, RRFReranker, XGBReranker, get_reranker
from docint.rank.retriever import ReferenceRetriever, load_corpus

pytestmark = pytest.mark.skipif(not (MODELS_DIR / "xgb_v1.json").exists() or not (DATA_DIR / "corpus.jsonl").exists(),
                                reason="run make_corpus.py and docint.rank.train first")


@pytest.fixture(scope="module")
def retr():
    from data_rank.make_corpus import StubEmbedder
    return ReferenceRetriever(load_corpus(DATA_DIR / "corpus.jsonl"), StubEmbedder())


def test_leakage_jd_sets_disjoint():
    meta = json.loads((MODELS_DIR / "xgb_v1.meta.json").read_text())
    jds = load_jds()
    test_ids = {j["jd_id"] for j in jds if j["split"] == "test"}
    assert len(test_ids) == 6
    assert not set(meta["train_jd_ids"]) & test_ids
    assert set(meta["test_jd_ids"]) == test_ids
    assert set(meta["train_jd_ids"]) == {j["jd_id"] for j in jds if j["split"] == "train"}


def test_training_dataset_never_contains_test_jds(retr):
    jds = [j for j in load_jds() if j["split"] == "train"]
    _, _, groups, _ = build_dataset(retr, jds, load_labels(), top_k=5)
    assert not set(groups) & {j["jd_id"] for j in load_jds() if j["split"] == "test"}


def test_feature_parity_train_vs_serving(retr):
    jd = load_jds()[3]
    job = jd_to_spec(jd)
    X, _, _, meta = build_dataset(retr, [jd], load_labels(), top_k=10)
    rr = get_reranker()
    evs = retr.search(job, "hybrid")[:10]
    Xs, _ = rr.features_matrix(job, evs)
    assert [m[1] for m in meta] == [e.candidate_id for e in evs]
    np.testing.assert_allclose(X, Xs)


def test_contributions_are_additive_and_scores_bounded(retr):
    job = jd_to_spec(load_jds()[2])
    evs = retr.search(job, "hybrid")[:20]
    rr = get_reranker()
    out = rr.rerank(job, evs)
    assert [o.rank for o in out] == list(range(1, len(out) + 1))
    assert all(0 <= o.match_score <= 100 for o in out)
    assert [o.match_score for o in out] == sorted((o.match_score for o in out), reverse=True)
    X, _ = rr.features_matrix(job, evs)
    raw = rr.booster.predict(xgb.DMatrix(X, feature_names=FEATURE_NAMES))
    by_id = {o.candidate_id: o for o in out}
    for ev, r in zip(evs, raw):
        o = by_id[ev.candidate_id]
        # SHAP additivity: sum of per-feature contributions + bias == raw prediction
        assert sum(o.feature_contributions.values()) + rr.last_bias == pytest.approx(float(r), abs=1e-2)
        assert set(o.feature_contributions) == set(FEATURE_NAMES)


def test_missing_model_falls_back_to_rrf(tmp_path):
    r = get_reranker(tmp_path)
    assert isinstance(r, RRFReranker) and r.is_fallback


def test_wrong_feature_schema_fails_loudly(tmp_path):
    for f in ("xgb_v1.json", "xgb_v1.meta.json"):
        shutil.copy(MODELS_DIR / f, tmp_path / f)
    (tmp_path / "feature_schema.json").write_text(json.dumps({"schema_version": "0", "features": FEATURE_NAMES[:-1]}))
    with pytest.raises(FeatureSchemaError):
        get_reranker(tmp_path)
    with pytest.raises(FeatureSchemaError):
        check_schema({"schema_version": "1", "features": list(reversed(FEATURE_NAMES))})


def test_monotone_more_qualification_never_lowers_the_score(retr):
    """v1.2: a higher degree or more years of experience must never LOWER a candidate's score."""
    from docint.rank.train import MONOTONE
    rr = get_reranker()
    job = jd_to_spec(load_jds()[5])
    X, _ = rr.features_matrix(job, retr.search(job, "hybrid")[:40])
    import xgboost as xgb
    base = rr.booster.predict(xgb.DMatrix(X, feature_names=FEATURE_NAMES))
    for name, direction in MONOTONE.items():
        j = FEATURE_NAMES.index(name)
        bumped = X.copy()
        bumped[:, j] += 1.0 if direction > 0 else -1.0  # a strictly "better" candidate on this feature
        new = rr.booster.predict(xgb.DMatrix(bumped, feature_names=FEATURE_NAMES))
        assert (new >= base - 1e-4).all(), name
