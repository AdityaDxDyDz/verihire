"""The ONLY place the backend imports ML *implementations* from ``docint``.

ML_MODE
  * ``stub`` - every component comes from ``docint.stubs`` (fast, deterministic, no models).
  * ``real`` - every component must come from the real modules; startup FAILS if one is missing.
               Required in production so a demo can never silently run on fake ML.
  * ``auto`` - per component: real if importable and not marked ``@unimplemented``, else stub.
               Lets the backend pick up Harsh's / Ekant's code the moment their PRs merge.
               Forbidden in production because a broken import would silently degrade to stubs.

Everything returned by the ML layer is re-validated into the contract models, so a small type
difference (dicts vs. models, extra fields) never crashes the API. Sync ML calls run in threads.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from docint import contracts as C
from docint import stubs

log = logging.getLogger("verihire.ml")

# component -> candidate (module, attribute) locations, tried in order. The package-level name is the
# contract; the sub-module paths match the layouts teammates actually shipped (e.g. Harsh's
# docint.ingest.ingest.run_ingest), so a missing re-export never silently degrades to a stub.
REAL_LOCATIONS: dict[str, list[tuple[str, str]]] = {
    "ingest": [("docint.ingest", "run_ingest"), ("docint.ingest.ingest", "run_ingest")],
    "embedder": [("docint.embed", "get_embedder"), ("docint.embed.embedder", "get_embedder")],
    "llm": [("docint.llm", "get_llm_client"), ("docint.llm.client", "get_llm_client")],
    "jd_parser": [("docint.llm", "parse_job_description"), ("docint.llm.jd_parser", "parse_job_description")],
    "skillgap": [("docint.llm", "analyze_skill_gap"), ("docint.llm.skillgap", "analyze_skill_gap")],
    "queries": [("docint.rank", "build_queries"), ("docint.rank.queries", "build_queries")],
    "reranker": [("docint.rank", "get_reranker"), ("docint.rank.reranker", "get_reranker")],
}
STUB_IMPLS: dict[str, Any] = {
    "ingest": stubs.run_ingest,
    "embedder": stubs.get_embedder,
    "llm": stubs.get_llm_client,
    "jd_parser": stubs.parse_job_description,
    "skillgap": stubs.analyze_skill_gap,
    "queries": stubs.build_queries,
    "reranker": stubs.get_reranker,
}


class MLConfigError(RuntimeError):
    pass


class EmbeddingDimError(RuntimeError):
    pass


def _is_unimplemented(obj: Any) -> bool:
    return bool(getattr(obj, "__docint_unimplemented__", False))


def _load_one(mod_name: str, attr: str) -> tuple[Any | None, Any | None, str]:
    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as e:
        if e.name and (e.name == mod_name or mod_name.startswith(e.name + ".")):
            return None, None, f"{mod_name} not present"
        return None, None, f"{mod_name} import failed: {e}"
    except Exception as e:  # noqa: BLE001 - a broken teammate module must not kill auto mode
        return None, None, f"{mod_name} import failed: {e!r}"
    fn = getattr(mod, attr, None)
    if fn is None:
        return None, mod, f"{mod_name}.{attr} missing"
    if _is_unimplemented(fn) or _is_unimplemented(mod):
        return None, mod, f"{mod_name}.{attr} marked unimplemented"
    return fn, mod, "ok"


def _load_real(component: str) -> tuple[Any | None, Any | None, str]:
    """Return (callable, module, reason) from the first location that works."""
    reasons = []
    for mod_name, attr in REAL_LOCATIONS[component]:
        fn, mod, reason = _load_one(mod_name, attr)
        if fn is not None:
            return fn, mod, reason
        reasons.append(reason)
        if "import failed" in reason or "unimplemented" in reason:
            break  # the module exists but is broken/placeholder: do not hide it behind another path
    return None, None, "; ".join(reasons)


def to_plain(obj: Any) -> Any:
    """Recursively turn pydantic models, dataclasses, NamedTuples and numpy values into plain Python."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, tuple) and hasattr(obj, "_asdict"):
        return {k: to_plain(v) for k, v in obj._asdict().items()}
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_plain(v) for v in obj]
    if hasattr(obj, "tolist") and hasattr(obj, "dtype"):  # numpy array / scalar
        return obj.tolist()
    return obj


def _normalise_profile(p: dict) -> dict:
    """Make a teammate's profile fit the frozen contract without losing information silently."""
    p = dict(p)
    if p.get("years_experience") is None:  # unknown (no dates in resume) -> 0 for ranking/filters
        p["years_experience"] = 0.0
    p["years_experience"] = max(0.0, min(60.0, float(p["years_experience"])))
    p["education_level"] = max(0, min(4, int(p.get("education_level") or 0)))
    p["skills"] = list(dict.fromkeys(str(x).strip().lower() for x in (p.get("skills") or []) if str(x).strip()))
    for key in ("titles", "education_fields", "certifications"):
        p[key] = [str(x) for x in (p.get(key) or []) if x is not None]
    return p


def _as(model: type, obj: Any):
    if isinstance(obj, model):
        return obj
    data = to_plain(obj)
    if model is C.ResumeProfile and isinstance(data, dict):
        data = _normalise_profile(data)
    if model is C.IngestResult and isinstance(data, dict) and isinstance(data.get("profile"), dict):
        data["profile"] = _normalise_profile(data["profile"])
    return model.model_validate(data)


def _is_stub_llm(llm: Any) -> bool:
    return "stub" in type(llm).__name__.lower() or str(getattr(llm, "model", "")).lower().startswith("stub")


class IngestOutput(NamedTuple):
    doc: C.ExtractedDoc
    result: C.IngestResult
    embed_texts: list[str]  # exactly what the ML layer says must be embedded, one per chunk


def _foreign_extraction_error(e: BaseException) -> C.ExtractionError | None:
    """Teammates may define their own ExtractionError class; map anything shaped like it."""
    if isinstance(e, C.ExtractionError):
        return e
    code = getattr(e, "code", None)
    if type(e).__name__ == "ExtractionError" and isinstance(code, str) and code:
        return C.ExtractionError(code, str(getattr(e, "message", "") or e))
    return None


@dataclass
class MLAdapter:
    mode: str
    expected_dim: int = C.EMBEDDING_DIM
    components: dict[str, str] = field(default_factory=dict)  # name -> "real" | "stub"
    _fns: dict[str, Any] = field(default_factory=dict)
    _mods: dict[str, Any] = field(default_factory=dict)
    embedder: Any = None
    reranker: Any = None
    llm: Any = None
    # PyMuPDF (and most PDF libraries) are not thread-safe: one extraction at a time. The lock is taken
    # in async code BEFORE the per-document timeout starts, so queueing never counts as processing time,
    # and a timed-out (hung) extraction releases it instead of blocking every document behind it.
    _ingest_alock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def load(cls, mode: str, expected_dim: int = C.EMBEDDING_DIM) -> MLAdapter:
        if mode not in ("stub", "real", "auto"):
            raise MLConfigError(f"unknown ML_MODE {mode!r}")
        self = cls(mode=mode, expected_dim=expected_dim)
        missing = []
        for comp in REAL_LOCATIONS:
            if mode == "stub":
                self._use_stub(comp)
                continue
            fn, mod, reason = _load_real(comp)
            if fn is None:
                if mode == "real":
                    missing.append(f"{comp}: {reason}")
                    continue
                log.warning("ML component %s -> stub (%s)", comp, reason)
                self._use_stub(comp)
            else:
                self._fns[comp], self._mods[comp], self.components[comp] = fn, mod, "real"
        if missing:
            raise MLConfigError("ML_MODE=real but these components are unavailable: " + "; ".join(missing))
        self._instantiate()
        return self

    def _use_stub(self, comp: str) -> None:
        self._fns[comp], self._mods[comp], self.components[comp] = STUB_IMPLS[comp], stubs, "stub"

    def _instantiate(self) -> None:
        for comp, attr in (("embedder", "embedder"), ("reranker", "reranker"), ("llm", "llm")):
            try:
                setattr(self, attr, self._fns[comp]())
            except Exception as e:  # noqa: BLE001
                if self.mode == "real" or self.components[comp] == "stub":
                    raise MLConfigError(f"could not create {comp}: {e!r}") from e
                log.warning("real %s failed to initialise (%r) -> stub", comp, e)
                self._use_stub(comp)
                setattr(self, attr, self._fns[comp]())
        if self.components.get("llm") == "real" and _is_stub_llm(self.llm):
            self.components["llm"] = "stub"  # e.g. Track C returns its rule-based StubLLM when no API key is set
        dim = int(getattr(self.embedder, "dim", -1))
        if dim != self.expected_dim:
            raise EmbeddingDimError(
                f"embedder {getattr(self.embedder, 'name', '?')} has dim {dim}, database expects {self.expected_dim}")

    # ------------------------------------------------------------------ info
    @property
    def stubs_in_use(self) -> bool:
        return any(v == "stub" for v in self.components.values())

    @property
    def embedder_name(self) -> str:
        return str(getattr(self.embedder, "name", type(self.embedder).__name__))

    @property
    def reranker_version(self) -> str:
        return str(getattr(self.reranker, "version", type(self.reranker).__name__))

    @property
    def llm_model(self) -> str:
        return str(getattr(self.llm, "model", None) or getattr(self.llm, "name", None) or type(self.llm).__name__)

    @property
    def reranker_is_fallback(self) -> bool:
        return bool(getattr(self.reranker, "is_fallback", False))

    @property
    def prompt_version(self) -> str:
        return str(getattr(self._mods.get("skillgap"), "PROMPT_VERSION", "unknown"))

    @property
    def pipeline_version(self) -> str:
        return str(getattr(self._mods.get("ingest"), "PIPELINE_VERSION", "unknown"))

    # ------------------------------------------------------------------ calls (sync)
    def run_ingest_sync(self, pdf: bytes, filename: str) -> IngestOutput:
        try:
            doc, res = self._fns["ingest"](pdf, filename)
        except Exception as e:
            mapped = _foreign_extraction_error(e)
            if mapped is not None and mapped is not e:
                raise mapped from e
            raise
        raw_chunks = list(getattr(res, "chunks", None) or (res.get("chunks", []) if isinstance(res, dict) else []))
        out_doc, out_res = _as(C.ExtractedDoc, doc), _as(C.IngestResult, res)
        texts = []
        for raw, ch in zip(raw_chunks, out_res.chunks, strict=True):
            fn = getattr(raw, "embedding_text", None)  # the ML layer decides what is embedded
            texts.append(fn() if callable(fn) else ch.embedding_text())
        return IngestOutput(out_doc, out_res, texts)

    def _check_vectors(self, vecs: list, n: int) -> list[list[float]]:
        out = [list(map(float, v)) for v in vecs]
        if len(out) != n:
            raise EmbeddingDimError(f"embedder returned {len(out)} vectors for {n} texts")
        for v in out:
            if len(v) != self.expected_dim:
                raise EmbeddingDimError(f"embedding has dim {len(v)}, expected {self.expected_dim}")
        return out

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._check_vectors(self.embedder.embed_documents(texts), len(texts))

    def embed_query_sync(self, text: str) -> list[float]:
        return self._check_vectors([self.embedder.embed_query(text)], 1)[0]

    def parse_job_description_sync(self, text: str) -> tuple[C.JobSpec, list[str]]:
        """Returns (spec, warnings). Falls back to the rule-based stub if the real parser fails."""
        try:
            spec = _as(C.JobSpec, self._fns["jd_parser"](text, self.llm))
            return spec, []
        except Exception as e:  # noqa: BLE001
            if self.components["jd_parser"] == "stub":
                raise
            log.warning("JD parser failed (%r) -> rule-based fallback", e)
            return _as(C.JobSpec, stubs.parse_job_description(text, None)), ["jd_parser_fallback"]

    def build_queries_sync(self, job: C.JobSpec) -> C.Queries:
        return _as(C.Queries, self._fns["queries"](job))

    def rerank_sync(self, job: C.JobSpec, cands: list[C.CandidateEvidence]) -> list[C.RankedCandidate]:
        fn = getattr(self.reranker, "rank", None) or getattr(self.reranker, "rerank", None)  # contract name / Track C name
        if fn is None:
            raise AttributeError(f"{type(self.reranker).__name__} has neither rank() nor rerank()")
        return [_as(C.RankedCandidate, r) for r in fn(job, cands)]

    def analyze_skill_gap_sync(self, job: C.JobSpec, profile: C.ResumeProfile,
                               chunks: list[C.RetrievedChunk]) -> C.SkillGapResult:
        return _as(C.SkillGapResult, self._fns["skillgap"](job, profile, chunks, self.llm))

    # ------------------------------------------------------------------ async wrappers
    async def run_ingest(self, pdf: bytes, filename: str, timeout: float | None = None) -> IngestOutput:
        async with self._ingest_alock:
            work = asyncio.to_thread(self.run_ingest_sync, pdf, filename)
            return await (asyncio.wait_for(work, timeout) if timeout else work)

    async def embed_documents(self, texts: list[str]):
        return await asyncio.to_thread(self.embed_documents_sync, texts)

    async def embed_query(self, text: str):
        return await asyncio.to_thread(self.embed_query_sync, text)

    async def parse_job_description(self, text: str):
        return await asyncio.to_thread(self.parse_job_description_sync, text)

    async def build_queries(self, job: C.JobSpec):
        return await asyncio.to_thread(self.build_queries_sync, job)

    async def rerank(self, job, cands):
        return await asyncio.to_thread(self.rerank_sync, job, cands)

    async def analyze_skill_gap(self, job, profile, chunks):
        return await asyncio.to_thread(self.analyze_skill_gap_sync, job, profile, chunks)
