"""Settings loaded from environment / .env (pydantic-settings).

Production safety: ``Settings.validate_for_production()`` refuses to boot with dev auth,
local storage, stub/auto ML, or a weak APP_SECRET.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

API_VERSION = "1.0.0"
DEFAULT_SECRET = "change-me"


class ConfigError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
    )

    APP_ENV: Literal["development", "test", "production"] = "development"
    AUTH_MODE: Literal["dev", "supabase"] = "dev"
    STORAGE_MODE: Literal["local", "supabase"] = "local"
    ML_MODE: Literal["auto", "stub", "real"] = "auto"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/verihire"
    DB_POOL_MIN: int = Field(default=1, ge=1)
    DB_POOL_MAX: int = Field(default=10, ge=1)
    AUTO_MIGRATE: bool = False  # run db/migrations at startup (handy on a fresh host)

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    STORAGE_BUCKET: str = "resumes"
    LOCAL_STORAGE_DIR: str = str(REPO_ROOT / ".data" / "storage")
    AUTH_CACHE_TTL_S: int = 300

    APP_SECRET: str = DEFAULT_SECRET
    EMBEDDING_DIM: int = 384

    MAX_PDF_MB: float = 10
    MAX_FILES_PER_REGISTER: int = 100
    INGEST_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    INGEST_WORKERS_ENABLED: bool = True
    INGEST_POLL_INTERVAL_S: float = 1.0
    INGEST_TIMEOUT_S: float = 60.0
    INGEST_MAX_ATTEMPTS: int = 3
    INGEST_STALE_AFTER_S: float = 120.0  # in-flight doc with no heartbeat for this long => reclaimed
    INGEST_HEARTBEAT_S: float = 10.0
    JANITOR_INTERVAL_S: float = 15.0
    # one backend container: on startup, docs left in-flight by the previous (killed) process are
    # reclaimed at once instead of waiting INGEST_STALE_AFTER_S. Set false when running replicas.
    INGEST_SINGLE_INSTANCE: bool = True

    RERANK_POOL: int = Field(default=50, ge=1, le=200)  # = Track C training/eval top_k (rerank top 50 retrieved)
    RETRIEVAL_LIMIT: int = Field(default=200, ge=10, le=2000)
    RRF_K: int = 60
    ANALYSIS_TIMEOUT_S: float = 90.0  # Track C retries the LLM up to 3x
    JD_PARSE_TIMEOUT_S: float = 45.0
    LLM_RATE_LIMIT_PER_MIN: int = 20
    PDF_URL_TTL_S: int = 300

    # ---- outreach e-mails (interview availability requests to shortlisted candidates)
    EMAIL_MODE: Literal["outbox", "smtp", "disabled"] = "outbox"  # outbox = write .eml files (dev), never sends
    EMAIL_FROM: str = "VeriHire <no-reply@localhost>"
    EMAIL_REPLY_TO: str = ""  # e.g. the recruiter's mailbox: candidates may also simply reply to the e-mail
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True  # port 587; set false and SMTP_SSL=true for port 465
    SMTP_SSL: bool = False
    SMTP_TIMEOUT_S: float = 20.0
    OUTBOX_DIR: str = str(REPO_ROOT / ".data" / "outbox")
    PUBLIC_APP_URL: str = "http://localhost:8000"  # base of the candidate's reply link: <url>/#/interview/<token>
    OUTREACH_WORKERS_ENABLED: bool = True
    OUTREACH_POLL_INTERVAL_S: float = 2.0
    OUTREACH_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    OUTREACH_SEND_PER_MINUTE: int = Field(default=30, ge=1, le=600)  # stay under the SMTP provider's limits
    OUTREACH_LINK_TTL_DAYS: int = Field(default=14, ge=1, le=90)
    OUTREACH_RATE_LIMIT_PER_MIN: int = 10  # send requests per recruiter per minute
    PUBLIC_RATE_LIMIT_PER_MIN: int = 20  # candidate reply-page calls per client IP per minute

    FRONTEND_DIR: str = str(REPO_ROOT / "frontend")
    CORS_ORIGINS: str = ""  # comma-separated; empty = same-origin only (frontend served by backend)

    @field_validator("SUPABASE_URL")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def max_pdf_bytes(self) -> int:
        return int(self.MAX_PDF_MB * 1024 * 1024)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_for_production(self) -> None:
        """Raise ConfigError for any unsafe combination. Called at startup in every env."""
        problems: list[str] = []
        if self.AUTH_MODE == "supabase" and not (self.SUPABASE_URL and self.SUPABASE_ANON_KEY):
            problems.append("AUTH_MODE=supabase needs SUPABASE_URL and SUPABASE_ANON_KEY")
        if self.STORAGE_MODE == "supabase" and not (self.SUPABASE_URL and self.SUPABASE_SERVICE_KEY):
            problems.append("STORAGE_MODE=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY")
        if self.EMAIL_MODE == "smtp" and not self.SMTP_HOST:
            problems.append("EMAIL_MODE=smtp needs SMTP_HOST (and usually SMTP_USERNAME / SMTP_PASSWORD)")
        if self.EMBEDDING_DIM != 384:
            problems.append("EMBEDDING_DIM is frozen at 384 (db column is vector(384))")
        if self.APP_ENV == "production":
            if self.AUTH_MODE != "supabase":
                problems.append("AUTH_MODE=dev is forbidden in production")
            if self.STORAGE_MODE != "supabase":
                problems.append("STORAGE_MODE must be supabase in production")
            if self.ML_MODE != "real":
                problems.append("ML_MODE must be 'real' in production (stubs forbidden)")
            if self.EMAIL_MODE == "outbox":
                problems.append("EMAIL_MODE=outbox only writes files; use smtp (or disabled) in production")
            if self.EMAIL_MODE == "smtp" and not self.PUBLIC_APP_URL.startswith("https://"):
                problems.append("PUBLIC_APP_URL must be the https address candidates open (reply links)")
            if self.APP_SECRET == DEFAULT_SECRET or len(self.APP_SECRET) < 32:
                problems.append("APP_SECRET must be a random string of >= 32 characters")
        if problems:
            raise ConfigError("; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
