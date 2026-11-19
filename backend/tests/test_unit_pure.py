"""Pure unit tests - no database needed. Expected values written by hand."""

from __future__ import annotations

import pytest

from app.config import ConfigError, Settings
from app.errors import AppError
from app.ratelimit import RateLimiter
from app.services.citations import ChunkRef, locate, normalise, verify_and_resolve
from app.services.csv_export import neutralise, results_to_csv
from app.services.documents import looks_like_pdf, safe_filename
from app.services.search import sanitise_ts_query
from docint.contracts import Citation, SkillAssessment, SkillGapResult


# ---------------------------------------------------------------- config
def _s(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


GOOD_PROD = dict(APP_ENV="production", AUTH_MODE="supabase", STORAGE_MODE="supabase", ML_MODE="real",
                 APP_SECRET="s" * 40, SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="anon",
                 SUPABASE_SERVICE_KEY="service")


def test_production_config_ok():
    _s(**GOOD_PROD).validate_for_production()


@pytest.mark.parametrize("override,needle", [
    ({"AUTH_MODE": "dev"}, "AUTH_MODE=dev"),
    ({"ML_MODE": "auto"}, "ML_MODE must be 'real'"),
    ({"ML_MODE": "stub"}, "ML_MODE must be 'real'"),
    ({"STORAGE_MODE": "local"}, "STORAGE_MODE"),
    ({"APP_SECRET": "change-me"}, "APP_SECRET"),
    ({"APP_SECRET": "short"}, "APP_SECRET"),
    ({"SUPABASE_SERVICE_KEY": ""}, "SUPABASE_SERVICE_KEY"),
    ({"EMBEDDING_DIM": 768}, "384"),
])
def test_production_config_refuses_unsafe(override, needle):
    with pytest.raises(ConfigError, match=needle):
        _s(**{**GOOD_PROD, **override}).validate_for_production()


def test_dev_config_ok_and_trailing_slash_stripped():
    s = _s(SUPABASE_URL="https://x.supabase.co/")
    s.validate_for_production()
    assert s.SUPABASE_URL == "https://x.supabase.co"
    assert s.max_pdf_bytes == 10 * 1024 * 1024


# ---------------------------------------------------------------- ts_query sanitising
@pytest.mark.parametrize("raw,expected", [
    ("python | fastapi", "python | fastapi"),
    ("c++ & node.js", "c | node | js"),
    ("python')); drop table x;--", "python | drop | table | x"),
    ("  ", ""),
    (None, ""),
    ("Python | python | PYTHON", "python"),
    ("a:* & !b <-> (c)", "a | b | c"),
])
def test_sanitise_ts_query(raw, expected):
    assert sanitise_ts_query(raw) == expected


def test_sanitise_ts_query_caps_terms():
    assert len(sanitise_ts_query(" ".join(f"t{i}" for i in range(100))).split(" | ")) == 25


# ---------------------------------------------------------------- uploads helpers
@pytest.mark.parametrize("raw,expected", [
    ("resume.pdf", "resume.pdf"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\cv.pdf", "cv.pdf"),
    ("  <evil>|name?.pdf ", "evilname.pdf"),
    ("", "unnamed.pdf"),
    (None, "unnamed.pdf"),
    ("bad\x00\x07name.pdf", "badname.pdf"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_truncates_keeping_extension():
    out = safe_filename("a" * 500 + ".pdf")
    assert len(out) <= 200 and out.endswith(".pdf")


def test_looks_like_pdf():
    assert looks_like_pdf(b"%PDF-1.7 ...")
    assert looks_like_pdf(b"\xef\xbb\xbf junk %PDF-1.4")
    assert not looks_like_pdf(b"PK\x03\x04 zip")
    assert not looks_like_pdf(b"")


# ---------------------------------------------------------------- CSV
@pytest.mark.parametrize("v,expected", [
    ("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("+1", "'+1"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"),
    ("\tx", "'\tx"), ("normal", "normal"), (None, ""), (42, "42"),
])
def test_csv_neutralise(v, expected):
    assert neutralise(v) == expected


def test_results_to_csv():
    out = results_to_csv([{"rank": 1, "label": "=cmd|' /C calc'!A0", "match_score": 88.5,
                           "matched_required": ["python", "sql"]}])
    assert out.startswith("\ufeffrank,label,match_score")
    assert "'=cmd" in out and "python; sql" in out


# ---------------------------------------------------------------- rate limiter
def test_rate_limiter_blocks_then_refills():
    now = [0.0]
    rl = RateLimiter(per_minute=2, clock=lambda: now[0])
    rl.check("u")
    rl.check("u")
    with pytest.raises(AppError) as e:
        rl.check("u")
    assert e.value.status == 429 and e.value.headers["Retry-After"] == "31"
    rl.check("other-user")  # separate bucket
    now[0] = 31.0
    rl.check("u")


# ---------------------------------------------------------------- citations
TEXT = "Experience\nBackend Engineer  (2020 – present)\n- Built “fast” APIs with   FastAPI and PostgreSQL.\n"


def test_normalise():
    assert normalise("  A\u00a0 B\n\nC  ") == "a b c"
    assert normalise("“x” – ‘y’") == "\"x\" - 'y'"


@pytest.mark.parametrize("quote", [
    "Built \"fast\" APIs with FastAPI",      # straight vs curly quotes
    "built “fast” apis with fastapi",        # case
    "APIs with FastAPI and PostgreSQL.",     # trailing punctuation
    "Engineer (2020 - present)",             # dash + double space
])
def test_locate_accepts_and_returns_exact_span(quote):
    span = locate(quote, TEXT)
    assert span is not None
    s, e = span
    assert normalise(TEXT[s:e]).strip(" .") == normalise(quote).strip(" .")


@pytest.mark.parametrize("quote", ["Built APIs with Django", "FastAPI and MySQL", "Py", "", "x" * 700])
def test_locate_rejects(quote):
    assert locate(quote, TEXT) is None


def _result(cits, status="present", dropped=0):
    return SkillGapResult(skills=[SkillAssessment(skill="fastapi", status=status, citations=cits)],
                          dropped_unverified_claims=dropped)


def test_verify_keeps_real_and_replaces_with_exact_text():
    chunks = {"c1": ChunkRef("c1", TEXT, 1, "experience")}
    clean, resolved = verify_and_resolve(_result([Citation(chunk_id="c1", quote="apis WITH fastapi")]), chunks)
    assert clean.dropped_unverified_claims == 0
    r = resolved[0]
    assert r["quote"] == TEXT[r["start"]:r["end"]] == "APIs with   FastAPI"
    assert clean.skills[0].citations[0].quote == r["quote"]
    assert clean.skills[0].status == "present"


def test_verify_drops_fabricated_and_foreign_chunks_and_downgrades():
    chunks = {"c1": ChunkRef("c1", TEXT, 1, "experience")}
    res = _result([Citation(chunk_id="c1", quote="Led a team of 50 engineers"),
                   Citation(chunk_id="other-candidates-chunk", quote="FastAPI"),
                   Citation(chunk_id="c1", quote="with FastAPI and PostgreSQL. Also Kubernetes")], dropped=2)
    clean, resolved = verify_and_resolve(res, chunks)
    assert resolved == []
    assert clean.dropped_unverified_claims == 2 + 3
    assert clean.skills[0].status == "missing"
    assert "No verifiable evidence" in clean.skills[0].reasoning


def test_verify_cross_chunk_quote_rejected():
    chunks = {"a": ChunkRef("a", "Skills: Python and", 1, "skills"), "b": ChunkRef("b", "FastAPI daily", 1, "skills")}
    clean, _ = verify_and_resolve(_result([Citation(chunk_id="a", quote="Python and FastAPI")]), chunks)
    assert clean.dropped_unverified_claims == 1


def test_verify_missing_status_stays_missing_without_citations():
    clean, resolved = verify_and_resolve(_result([], status="missing"), {})
    assert clean.skills[0].status == "missing" and resolved == []
