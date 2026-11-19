"""ML_MODE resolution (stub / real / auto) and output normalisation - no DB needed."""

from __future__ import annotations

import sys
import types

import pytest

from app.adapters.ml import REAL_LOCATIONS, EmbeddingDimError, MLAdapter, MLConfigError
from docint import stubs


@pytest.fixture
def fake_modules(monkeypatch):
    """Install fake docint.ingest / docint.embed / docint.rank / docint.llm modules."""
    made = {}

    def install(name: str, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)
        made[name] = mod
        return mod

    yield install


def _hide_real_modules(monkeypatch):
    """Pretend no teammate code exists (also when Track B is merged), including every fallback location."""
    names = {mod for locs in REAL_LOCATIONS.values() for mod, _ in locs}
    names |= {"docint.ingest", "docint.embed", "docint.rank", "docint.llm"}
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)  # makes import raise ModuleNotFoundError


def test_stub_mode_uses_only_stubs():
    ml = MLAdapter.load("stub")
    assert set(ml.components.values()) == {"stub"} and ml.stubs_in_use
    assert ml.embedder.dim == 384 and ml.prompt_version == stubs.PROMPT_VERSION


def test_auto_mode_falls_back_when_modules_missing(monkeypatch):
    _hide_real_modules(monkeypatch)
    ml = MLAdapter.load("auto")
    assert set(ml.components.values()) == {"stub"}


def test_real_mode_refuses_to_start_without_real_components(monkeypatch):
    _hide_real_modules(monkeypatch)
    with pytest.raises(MLConfigError, match="ML_MODE=real"):
        MLAdapter.load("real")


def test_unknown_mode():
    with pytest.raises(MLConfigError):
        MLAdapter.load("magic")


def test_finds_run_ingest_in_a_submodule(monkeypatch, fake_modules):
    """Harsh shipped docint.ingest.ingest.run_ingest without re-exporting it from docint.ingest."""
    _hide_real_modules(monkeypatch)
    fake_modules("docint.ingest")  # package exists but does not export run_ingest
    fake_modules("docint.ingest.ingest", run_ingest=stubs.run_ingest, PIPELINE_VERSION="tb-1")
    ml = MLAdapter.load("auto")
    assert ml.components["ingest"] == "real" and ml.pipeline_version == "tb-1"


def test_dataclass_outputs_none_years_and_foreign_errors(monkeypatch, fake_modules):
    import dataclasses

    from docint.contracts import ExtractionError

    @dataclasses.dataclass(frozen=True)
    class DChunk:
        chunk_index: int
        section: str
        page_no: int
        char_start: int
        char_end: int
        text_original: str
        text_scrubbed: str
        token_count: int

        def embedding_text(self):
            return "CUSTOM:" + self.text_scrubbed

    @dataclasses.dataclass(frozen=True)
    class DProfile:
        skills: list
        years_experience: float | None = None
        education_level: int = 7

    @dataclasses.dataclass(frozen=True)
    class DDoc:
        pages: list
        page_count: int
        is_scanned: bool
        suspicious_hidden_text: bool

    @dataclasses.dataclass(frozen=True)
    class DPii:
        full_name: str | None = None

    @dataclasses.dataclass(frozen=True)
    class DRes:
        pii: DPii
        chunks: list
        profile: DProfile

    class TheirExtractionError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code, self.message = code, message

    TheirExtractionError.__name__ = "ExtractionError"

    def run_ingest(pdf, name):
        if pdf == b"bad":
            raise TheirExtractionError("corrupt", "cannot open")
        return (DDoc([{"page_no": 1, "text": "Skills Python"}], 1, False, False),
                DRes(DPii("X"), [DChunk(0, "skills", 1, 0, 13, "Skills Python", "Skills Python", 2)],
                     DProfile(["Python", " SQL "])))

    _hide_real_modules(monkeypatch)
    fake_modules("docint.ingest", run_ingest=run_ingest)
    ml = MLAdapter.load("auto")
    out = ml.run_ingest_sync(b"%PDF", "a.pdf")
    assert out.result.profile.years_experience == 0.0 and out.result.profile.education_level == 4
    assert out.result.profile.skills == ["python", "sql"]
    assert out.embed_texts == ["CUSTOM:Skills Python"]  # the ML layer decides what is embedded
    with pytest.raises(ExtractionError) as e:
        ml.run_ingest_sync(b"bad", "a.pdf")
    assert e.value.code == "corrupt"


def test_auto_picks_up_real_components_and_respects_unimplemented(monkeypatch, fake_modules):
    _hide_real_modules(monkeypatch)

    class RealEmbedder(stubs.StubEmbedder):
        name = "BAAI/bge-small-en-v1.5"

    fake_modules("docint.embed", get_embedder=lambda: RealEmbedder())
    fake_modules("docint.ingest", run_ingest=stubs.unimplemented(lambda pdf, fn: None), PIPELINE_VERSION="h-1")
    ml = MLAdapter.load("auto")
    assert ml.components["embedder"] == "real" and ml.embedder_name == "BAAI/bge-small-en-v1.5"
    assert ml.components["ingest"] == "stub"  # placeholder marked @unimplemented


def test_auto_survives_a_broken_teammate_module(monkeypatch, fake_modules):
    _hide_real_modules(monkeypatch)

    def bad_factory():
        raise OSError("model download failed")

    fake_modules("docint.rank", get_reranker=bad_factory, build_queries=stubs.build_queries)
    ml = MLAdapter.load("auto")
    assert ml.components["reranker"] == "stub" and ml.components["queries"] == "real"


def test_real_mode_with_all_components(monkeypatch, fake_modules):
    _hide_real_modules(monkeypatch)
    fake_modules("docint.ingest", run_ingest=stubs.run_ingest, PIPELINE_VERSION="harsh-1.0")
    fake_modules("docint.embed", get_embedder=stubs.get_embedder)
    fake_modules("docint.rank", get_reranker=stubs.get_reranker, build_queries=stubs.build_queries)
    class FakeClaude:
        model = "claude-test"

    fake_modules("docint.llm", get_llm_client=FakeClaude, parse_job_description=stubs.parse_job_description,
                 analyze_skill_gap=stubs.analyze_skill_gap, PROMPT_VERSION="skillgap_v1")
    ml = MLAdapter.load("real")
    assert set(ml.components.values()) == {"real"} and not ml.stubs_in_use and ml.llm_model == "claude-test"
    assert ml.pipeline_version == "harsh-1.0" and ml.prompt_version == "skillgap_v1"


def test_rule_based_llm_is_reported_as_stub(monkeypatch, fake_modules):
    """Track C returns a rule-based StubLLM when no API key is set: /health must say so."""
    _hide_real_modules(monkeypatch)

    class StubLLM:
        model = "stub-llm"

    fake_modules("docint.llm", get_llm_client=StubLLM, parse_job_description=stubs.parse_job_description,
                 analyze_skill_gap=stubs.analyze_skill_gap)
    ml = MLAdapter.load("auto")
    assert ml.components["llm"] == "stub" and ml.components["skillgap"] == "real"


def test_reranker_with_rerank_method_only(monkeypatch, fake_modules):
    """Track C's reranker exposes rerank(); the contract says rank(). Both must work."""
    _hide_real_modules(monkeypatch)

    class OnlyRerank:
        version = "x"

        def rerank(self, job, cands):
            return stubs.StubReranker().rank(job, cands)

    fake_modules("docint.rank", get_reranker=OnlyRerank, build_queries=stubs.build_queries)
    ml = MLAdapter.load("auto")
    assert ml.rerank_sync(stubs.parse_job_description("Python developer with FastAPI and SQL experience"), []) == []


def test_wrong_embedder_dimension_fails_at_startup(monkeypatch, fake_modules):
    _hide_real_modules(monkeypatch)

    class E768:
        name, dim = "big", 768

    fake_modules("docint.embed", get_embedder=lambda: E768())
    with pytest.raises(EmbeddingDimError, match="768"):
        MLAdapter.load("auto")


def test_outputs_are_normalised_from_dicts():
    ml = MLAdapter.load("stub")
    ml._fns["queries"] = lambda job: {"dense_query": "x", "ts_query": "a | b", "extra_field": 1}
    q = ml.build_queries_sync(stubs.parse_job_description("Python developer needed with FastAPI skills and SQL"))
    assert q.ts_query == "a | b"


def test_embedding_vector_count_checked():
    ml = MLAdapter.load("stub")

    class Short(stubs.StubEmbedder):
        def embed_documents(self, texts):
            return super().embed_documents(texts)[:-1]

    ml.embedder = Short()
    with pytest.raises(EmbeddingDimError):
        ml.embed_documents_sync(["a", "b"])
    assert ml.embed_documents_sync([]) == []
