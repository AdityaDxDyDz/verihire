"""Frozen data contracts between the backend and the ML package (``docint``).

SHARED FILE — change only via a ``contract-change`` PR approved by all four.
Rules (plan §7.2):
  * embedding dim = 384 (BGE-small)
  * ``text_original == page_text[char_start:char_end]``
  * scrubbed text never contains e-mail / phone / URL patterns
  * every citation is a verbatim substring of its chunk's ``text_scrubbed``
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.2.0"  # 1.2.0: RetrievedChunk accepts flat + nested shapes (additive)
EMBEDDING_DIM = 384


class _Model(BaseModel):
    # ignore unknown fields so an additive change on one side never breaks the other
    model_config = ConfigDict(extra="ignore")


class ExtractionError(Exception):
    """Raised by ``run_ingest`` for files that cannot be processed.

    ``code`` is a snake_case reason: corrupt | encrypted | empty | too_many_pages | timeout | ...
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}")


# ---------------------------------------------------------------- ingestion
class Page(_Model):
    page_no: int = Field(ge=1)
    text: str


class ExtractedDoc(_Model):
    pages: list[Page]
    page_count: int = Field(ge=0)
    is_scanned: bool = False
    suspicious_hidden_text: bool = False


class PiiEntities(_Model):
    full_name: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


class Chunk(_Model):
    chunk_index: int = Field(ge=0)
    section: str = "other"
    page_no: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text_original: str
    text_scrubbed: str
    token_count: int = Field(ge=0, default=0)

    def embedding_text(self) -> str:
        """The exact string that gets embedded (scrubbed text only - matches Track B's Chunk)."""
        return self.text_scrubbed


class ResumeProfile(_Model):
    skills: list[str] = Field(default_factory=list)  # canonical, lowercase
    titles: list[str] = Field(default_factory=list)
    years_experience: float = Field(ge=0, default=0.0)
    education_level: int = Field(ge=0, le=4, default=0)  # 0 none,1 diploma,2 bachelor,3 master,4 phd
    education_fields: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    summary: str | None = None
    parser_version: str = "unknown"


class IngestResult(_Model):
    pii: PiiEntities
    chunks: list[Chunk]
    profile: ResumeProfile


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# ---------------------------------------------------------------- ranking
class JobSpec(_Model):
    title: str | None = None
    raw_text: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    min_years: float | None = Field(default=None, ge=0)
    min_education: int | None = Field(default=None, ge=0, le=4)
    responsibilities: list[str] = Field(default_factory=list)


class Queries(_Model):
    dense_query: str
    ts_query: str = ""  # OR-joined, sanitised: "python | fastapi"


class RetrievedChunk(_Model):
    """A chunk returned by retrieval, in BOTH shapes the tracks use (contract v1.2.0):

    * flat (backend):  ``rc.text_scrubbed``, ``rc.section``, ``rc.page_no`` ...
    * nested (Track C): ``rc.chunk.text_scrubbed``, ``rc.chunk.section`` ...

    Give either one; the validator fills in the other. At serving time the backend has no
    ``text_original`` (PII is never read back), so the nested ``chunk.text_original`` is the
    scrubbed text - ML code must only ever use ``text_scrubbed``.
    """

    chunk_id: str
    document_id: str = ""
    candidate_id: str = ""
    chunk_index: int = 0
    section: str = "other"
    page_no: int = 1
    text_scrubbed: str = ""
    chunk: Chunk | None = None
    rrf_score: float = 0.0
    vec_rank: int | None = None
    fts_rank: int | None = None
    vec_sim: float | None = None

    @model_validator(mode="after")
    def _sync_shapes(self) -> RetrievedChunk:
        if self.chunk is not None:
            if not self.text_scrubbed:
                self.text_scrubbed = self.chunk.text_scrubbed
                self.section = self.chunk.section
                self.page_no = self.chunk.page_no
                self.chunk_index = self.chunk.chunk_index
        else:
            self.chunk = Chunk(chunk_index=self.chunk_index, section=self.section, page_no=self.page_no,
                               char_start=0, char_end=len(self.text_scrubbed), text_original=self.text_scrubbed,
                               text_scrubbed=self.text_scrubbed, token_count=len(self.text_scrubbed.split()))
        return self


class CandidateEvidence(_Model):
    candidate_id: str
    profile: ResumeProfile
    chunks: list[RetrievedChunk]
    rrf_sum_top3: float = 0.0
    rrf_max: float = 0.0
    n_chunks_hit: int = 0


class RankedCandidate(_Model):
    candidate_id: str
    match_score: float = Field(ge=0, le=100)
    rank: int = Field(ge=1)
    feature_values: dict[str, float] = Field(default_factory=dict)
    feature_contributions: dict[str, float] = Field(default_factory=dict)
    cross_encoder_score: float | None = None


class Reranker(Protocol):
    """``rank`` is the contract name; Track C's ``rerank`` is accepted as an alias by the backend."""

    version: str

    def rank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]: ...


# ---------------------------------------------------------------- LLM
class Citation(_Model):
    chunk_id: str
    quote: str


class SkillAssessment(_Model):
    skill: str
    status: Literal["present", "partial", "missing"]
    reasoning: str = ""
    citations: list[Citation] = Field(default_factory=list)


class SkillGapResult(_Model):
    summary: str = ""
    skills: list[SkillAssessment] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)
    dropped_unverified_claims: int = 0
    model: str = "unknown"
    prompt_version: str = "unknown"


class LLMClient(Protocol):
    name: str
