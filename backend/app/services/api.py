"""Pydantic models for every REST request/response (plan §7.1).

SHARED / FROZEN at Gate G1 — Kadam's fixtures in ``docs/contracts/fixtures`` must validate
against these models (``backend/tests/test_contract_fixtures.py``). Additive optional fields only.
Imports only *types* from ``docint.contracts`` (never ML implementations).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docint.contracts import JobSpec, ResumeProfile, SkillGapResult


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _In(BaseModel):
    # tolerate extra fields from the client; never fail on an additive frontend change
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


DocStatus = Literal["uploaded", "extracting", "embedding", "ready", "failed", "needs_ocr"]
# v1.3 (frontend integration): a job is "running" until every document is final; "completed_with_errors" when some
# documents failed / need OCR (the UI keeps polling while status == "running")
JobStatus = Literal["running", "completed", "completed_with_errors"]


# ------------------------------------------------------------------ errors
class ErrorBody(_Out):
    code: str
    message: str


class ErrorResponse(_Out):
    error: ErrorBody


# ------------------------------------------------------------------ health / me
class HealthResponse(_Out):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "down"]
    embedder: str
    reranker: str
    contract_version: str
    ml_mode: str
    ml_components: dict[str, str]
    stubs_in_use: bool
    auth_mode: str
    storage_mode: str
    app_env: str | None = None
    api_version: str | None = None
    prompt_version: str | None = None
    pipeline_version: str | None = None
    llm_model: str | None = None


class MeResponse(_Out):
    id: str
    email: str | None = None


# ------------------------------------------------------------------ documents / jobs
class RejectedFile(_Out):
    filename: str
    reason: str  # snake_case code: not_pdf | too_large | empty_file | duplicate | bad_filename
    message: str | None = None


class UploadResponse(_Out):
    job_id: str | None
    document_ids: list[str]
    rejected: list[RejectedFile]


class RetryRequest(_In):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class RetryResponse(_Out):
    job_id: str | None
    document_ids: list[str] = Field(default_factory=list)


class JobDocument(_Out):
    id: str
    filename: str
    status: DocStatus
    error: str | None = None


class JobResponse(_Out):
    id: str
    status: JobStatus
    total: int
    done: int
    failed: int
    documents: list[JobDocument]
    created_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentItem(_Out):
    id: str
    filename: str
    doc_type: str
    status: DocStatus
    size_bytes: int
    page_count: int | None = None
    error: str | None = None
    error_code: str | None = None
    candidate_id: str | None = None
    suspicious_hidden_text: bool = False
    created_at: datetime
    updated_at: datetime
    years_experience: float | None = None  # from the parsed profile once ready (library columns)
    education_level: int | None = None
    skills: list[str] = Field(default_factory=list)


class DocumentListResponse(_Out):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


class PdfUrlResponse(_Out):
    url: str
    expires_in: int


# ------------------------------------------------------------------ job descriptions
class JobDescriptionCreate(_In):
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=40, max_length=20000)


class JobDescriptionUpdate(_In):
    title: str | None = Field(default=None, max_length=200)
    spec: JobSpec


class JobDescriptionResponse(_Out):
    id: str
    title: str | None
    spec: JobSpec
    created_at: datetime
    updated_at: datetime


class JobDescriptionListItem(JobDescriptionResponse):
    required_skills: list[str] = Field(default_factory=list)  # = spec.required_skills (list view)
    last_search_id: str | None = None


class JobDescriptionListResponse(_Out):
    items: list[JobDescriptionListItem]


# ------------------------------------------------------------------ search
class SearchFilters(_In):
    min_years: float | None = Field(default=None, ge=0, le=60)
    min_education: int | None = Field(default=None, ge=0, le=4)
    required_skills: list[str] = Field(default_factory=list, max_length=50)


class SearchRequest(_In):
    jd_id: str
    top_k: int = Field(default=10, ge=1, le=100)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    blind: bool = True


class TopChunk(_Out):
    chunk_id: str
    page_no: int
    section: str
    text_scrubbed: str


class Explanation(_Out):
    feature_contributions: dict[str, float] = Field(default_factory=dict)
    feature_values: dict[str, float] = Field(default_factory=dict)
    cross_encoder_score: float | None = None


class RetrievalInfo(_Out):
    rrf_sum_top3: float
    n_chunks_hit: int


class Result(_Out):
    candidate_id: str
    document_id: str
    label: str
    match_score: float = Field(ge=0, le=100)  # relative ranking score, NOT a probability
    rank: int
    years_experience: float
    education_level: int
    titles: list[str]
    top_skills: list[str]
    matched_required: list[str]
    missing_required: list[str]
    matched_preferred: list[str]
    missing_preferred: list[str]
    suspicious_hidden_text: bool
    top_chunks: list[TopChunk]
    explanation: Explanation
    retrieval: RetrievalInfo
    display_name: str | None = None  # only when blind=false (audited)


class SearchResponse(_Out):
    search_id: str
    results: list[Result]
    timings: dict[str, float]
    warnings: list[str]
    reranker_version: str
    embedder: str
    job_spec: JobSpec
    blind: bool = True
    created_at: datetime | None = None
    jd_id: str | None = None


# ------------------------------------------------------------------ analysis
class AnalysisRequest(_In):
    jd_id: str
    candidate_id: str
    search_id: str | None = None


class ResolvedCitation(_Out):
    skill: str
    chunk_id: str
    quote: str  # == text_scrubbed[start:end], exactly
    text_scrubbed: str
    page_no: int
    section: str
    start: int
    end: int


class AnalysisResponse(_Out):
    result: SkillGapResult
    citations_resolved: list[ResolvedCitation]
    from_cache: bool


# ------------------------------------------------------------------ candidates
class PiiOut(_Out):
    full_name: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    email: str | None = None  # first of emails / phones (what the reveal panel shows)
    phone: str | None = None


class CandidateResponse(_Out):
    id: str
    label: str
    document_id: str | None = None
    profile: ResumeProfile
    suspicious_hidden_text: bool
    pii: PiiOut | None = None
    years_experience: float | None = None  # = profile.years_experience (flat copy for the UI)
    education_level: int | None = None


# ------------------------------------------------------------------ feedback / audit
class FeedbackRequest(_In):
    search_id: str
    candidate_id: str
    rating: Literal[-1, 1]
    note: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(_Out):
    id: str
    search_id: str
    candidate_id: str
    rating: int
    note: str | None = None
    created_at: datetime


class AuditEntry(_Out):
    id: int
    action: str
    target_type: str | None = None
    target_id: str | None = None
    details: dict = Field(default_factory=dict)
    created_at: datetime


class AuditListResponse(_Out):
    items: list[AuditEntry]


# ------------------------------------------------------------------ outreach: interview-availability e-mails (v1.4)
OutreachStatus = Literal["queued", "sending", "sent", "failed"]


class InterviewRequestCreate(_In):
    jd_id: str
    candidate_ids: list[str] = Field(min_length=1, max_length=100, description="the shortlisted candidates")
    company_name: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=2000, description="optional note added to every e-mail")
    reply_by: date | None = Field(default=None, description="deadline shown in the e-mail; the link lives at least "
                                                           "until then (default OUTREACH_LINK_TTL_DAYS)")
    resend: bool = Field(default=False, description="send again to candidates already invited for this JD")

    @field_validator("reply_by")
    @classmethod
    def _future(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("reply_by must not be in the past")
        return v


class InterviewRequestItem(_Out):
    candidate_id: str
    label: str
    status: Literal["queued", "skipped_no_email", "already_invited"]
    email_masked: str | None = None  # "p***@gmail.com"; never the full address
    message_id: str | None = None


class InterviewRequestBatchResponse(_Out):
    batch_id: str
    jd_id: str
    queued: int
    skipped_no_email: int
    already_invited: int
    items: list[InterviewRequestItem]


class InterviewReplyOut(_Out):
    slots: list[dict]
    timezone: str
    note: str | None = None
    created_at: datetime


class OutreachMessageItem(_Out):
    message_id: str
    batch_id: str
    jd_id: str
    candidate_id: str
    label: str
    email_masked: str
    status: OutreachStatus
    attempts: int
    error: str | None = None
    sent_at: datetime | None = None
    expires_at: datetime
    created_at: datetime
    reply: InterviewReplyOut | None = None  # latest reply from the candidate, if any


class OutreachListResponse(_Out):
    items: list[OutreachMessageItem]


class OutreachBatchResponse(_Out):
    batch_id: str
    jd_id: str
    company_name: str | None = None
    message: str | None = None
    reply_by: date | None = None
    created_at: datetime
    counts: dict[str, int]
    done: bool  # nothing queued or sending any more
    items: list[OutreachMessageItem]


class InterviewSlot(_In):
    start: datetime
    end: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> InterviewSlot:
        if self.start.tzinfo is None:
            raise ValueError("start must include a time-zone offset, e.g. 2026-12-03T10:00:00+05:30")
        if self.end is not None and (self.end.tzinfo is None or self.end <= self.start):
            raise ValueError("end must be after start and include a time-zone offset")
        return self


class InterviewReplyCreate(_In):
    slots: list[InterviewSlot] = Field(min_length=1, max_length=5)
    timezone: str = Field(min_length=1, max_length=64, description="IANA name shown back to the recruiter, "
                                                                   "e.g. Asia/Kolkata")
    note: str | None = Field(default=None, max_length=1000)


class InterviewReplyResponse(_Out):
    reply_id: str
    created_at: datetime
    slots: list[dict]
    timezone: str


class PublicInvitationResponse(_Out):
    role_title: str
    company_name: str | None = None
    message: str | None = None
    reply_by: date | None = None
    expires_at: datetime
    expired: bool
    last_reply: InterviewReplyOut | None = None
