"""Stand-in ("stub") implementations of every ML entry point.

SHARED FILE (frozen, see contracts.py). The backend runs entirely on these when
``ML_MODE=stub`` and falls back to them per component when ``ML_MODE=auto``.

They are deliberately simple and deterministic, but *behave* like the real thing:
  * the embedder is a feature-hashing bag-of-words, so lexical overlap gives
    higher cosine similarity (searches return sensible results);
  * ingestion really extracts text from simple PDFs, scrubs PII and keeps offsets;
  * the skill-gap stub only quotes text that exists in the chunk.
No third-party dependencies beyond pydantic (PyMuPDF / pypdf used if installed).
"""

from __future__ import annotations

import hashlib
import math
import re
import zlib
from typing import Any

from .contracts import (
    EMBEDDING_DIM,
    CandidateEvidence,
    Chunk,
    Citation,
    ExtractedDoc,
    ExtractionError,
    IngestResult,
    JobSpec,
    Page,
    PiiEntities,
    Queries,
    RankedCandidate,
    ResumeProfile,
    RetrievedChunk,
    SkillAssessment,
    SkillGapResult,
)

PROMPT_VERSION = "stub-skillgap-v1"
PIPELINE_VERSION = "stub-ingest-v1"


def unimplemented(obj: Any) -> Any:
    """Mark a placeholder so the backend's ``ML_MODE=auto`` falls back to the stub."""
    try:
        obj.__docint_unimplemented__ = True
    except (AttributeError, TypeError):
        pass
    return obj


# ------------------------------------------------------------------ skills
STUB_SKILLS: dict[str, list[str]] = {
    "python": ["python"], "java": ["java"], "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"], "go": ["golang"], "c++": ["c++", "cpp"], "c#": ["c#", "csharp"],
    "rust": ["rust"], "sql": ["sql"], "postgresql": ["postgresql", "postgres"], "mysql": ["mysql"],
    "mongodb": ["mongodb", "mongo"], "redis": ["redis"], "django": ["django"], "fastapi": ["fastapi"],
    "flask": ["flask"], "spring": ["spring", "spring boot"], "node.js": ["node.js", "nodejs", "node"],
    "react": ["react", "react.js", "reactjs"], "angular": ["angular"], "vue": ["vue", "vue.js"],
    "html": ["html"], "css": ["css"], "docker": ["docker"], "kubernetes": ["kubernetes", "k8s"],
    "aws": ["aws", "amazon web services"], "gcp": ["gcp", "google cloud"], "azure": ["azure"],
    "terraform": ["terraform"], "linux": ["linux"], "git": ["git"], "ci/cd": ["ci/cd", "cicd"],
    "machine learning": ["machine learning", "ml"], "deep learning": ["deep learning"],
    "pytorch": ["pytorch"], "tensorflow": ["tensorflow"], "scikit-learn": ["scikit-learn", "sklearn"],
    "pandas": ["pandas"], "numpy": ["numpy"], "nlp": ["nlp", "natural language processing"],
    "spark": ["spark", "pyspark"], "kafka": ["kafka"], "airflow": ["airflow"], "tableau": ["tableau"],
    "power bi": ["power bi"], "excel": ["excel"], "rest api": ["rest api", "rest apis", "restful"],
    "graphql": ["graphql"], "microservices": ["microservices"], "figma": ["figma"],
    "selenium": ["selenium"], "android": ["android"], "kotlin": ["kotlin"], "swift": ["swift"],
    "flutter": ["flutter"], "xgboost": ["xgboost"], "llm": ["llm", "llms"],
}
_ALIAS_TO_CANON = {alias: canon for canon, aliases in STUB_SKILLS.items() for alias in [canon, *aliases]}
# longest aliases first so "spring boot" wins over "spring"
_SKILL_RE = re.compile(
    r"(?<![\w+#.])(" + "|".join(re.escape(a) for a in sorted(_ALIAS_TO_CANON, key=len, reverse=True)) + r")(?![\w+#])",
    re.IGNORECASE,
)


def _track_b_extractor():
    """Use Harsh's skills dictionary when it is installed, so stub JD parsing and real ingestion
    produce the same canonical names (plan: ONE skills dictionary)."""
    try:
        from .ingest.skills import extract_skills  # type: ignore[import-not-found]
        return extract_skills
    except Exception:  # noqa: BLE001
        return None


_EXTRACT = _track_b_extractor()


def find_skills(text: str) -> list[str]:
    if _EXTRACT is not None:
        try:
            return [s.lower() for s in _EXTRACT(text)]
        except Exception:  # noqa: BLE001
            pass
    seen: dict[str, None] = {}
    for m in _SKILL_RE.finditer(text):
        alias = m.group(1).lower()
        if alias == "go" or alias == "ts":  # too ambiguous in prose
            continue
        seen.setdefault(_ALIAS_TO_CANON[alias], None)
    return list(seen)


def canonical_skill(s: str) -> str:
    s = s.strip().lower()
    return _ALIAS_TO_CANON.get(s, s)


# ------------------------------------------------------------------ embedder
_TOKEN_RE = re.compile(r"[a-z0-9+#]+")


class StubEmbedder:
    """Deterministic feature-hashing embedder (dim 384, L2-normalised)."""

    name = "stub-hash-384"
    dim = EMBEDDING_DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = _TOKEN_RE.findall(text.lower())
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = hashlib.blake2b(f.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            v[idx] += sign * (1.0 if "_" not in f else 0.5)
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:  # never return a zero vector (cosine would be NaN)
            v[0] = 1.0
            return v
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embedder() -> StubEmbedder:
    return StubEmbedder()


# ------------------------------------------------------------------ PDF text
def _pdf_text_naive(data: bytes) -> list[str]:
    """Tiny extractor for simple PDFs (uncompressed or Flate streams, Tj/TJ operators)."""
    pages: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        header = data[max(0, m.start() - 300): m.start()]
        if b"FlateDecode" in header:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                continue
        if b"BT" not in raw:
            continue
        lines: list[str] = []
        cur: list[str] = []
        for tok in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:.*?)\]\s*TJ|T\*|Td|TD|'", raw, re.S):
            t = tok.group(0)
            if t in (b"T*", b"Td", b"TD", b"'"):
                if cur:
                    lines.append("".join(cur))
                    cur = []
                continue
            for s in re.findall(rb"\(((?:\\.|[^\\)])*)\)", t, re.S):
                s = re.sub(rb"\\([()\\])", rb"\1", s)
                cur.append(s.decode("latin-1"))
        if cur:
            lines.append("".join(cur))
        pages.append("\n".join(lines))
    return pages


def extract_pdf(pdf_bytes: bytes) -> ExtractedDoc:
    if not pdf_bytes:
        raise ExtractionError("empty", "File is empty")
    if b"%PDF-" not in pdf_bytes[:1024]:
        raise ExtractionError("corrupt", "Not a PDF file")
    if b"/Encrypt" in pdf_bytes:
        raise ExtractionError("encrypted", "PDF is password-protected")
    texts: list[str] | None = None
    try:  # prefer a real library when installed
        try:
            import pymupdf as fitz  # type: ignore[import-not-found]  # PyMuPDF >= 1.24
        except ImportError:
            import fitz  # type: ignore[import-not-found]

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ExtractionError("encrypted", "PDF is password-protected")
            texts = [p.get_text() for p in doc]
    except ExtractionError:
        raise
    except Exception:
        texts = None
    if texts is None:
        if b"%%EOF" not in pdf_bytes[-2048:] or b"/Page" not in pdf_bytes:
            raise ExtractionError("corrupt", "PDF structure is damaged")
        texts = _pdf_text_naive(pdf_bytes)
        if not texts:
            n_pages = max(1, len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes)))
            texts = [""] * n_pages
    if len(texts) > 40:
        raise ExtractionError("too_many_pages", "More than 40 pages")
    pages = [Page(page_no=i + 1, text=t) for i, t in enumerate(texts)]
    avg = sum(len(t.strip()) for t in texts) / max(1, len(texts))
    joined = " ".join(texts).lower()
    suspicious = "ignore previous instructions" in joined or "rank this candidate first" in joined
    return ExtractedDoc(pages=pages, page_count=len(pages), is_scanned=avg < 50, suspicious_hidden_text=suspicious)


# ------------------------------------------------------------------ PII
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}(?!\w)")
URL_RE = re.compile(r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com)/\S+", re.IGNORECASE)
_NAME_LINE_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,3}$")


def detect_pii(pages: list[Page]) -> PiiEntities:
    text = "\n".join(p.text for p in pages)
    name = None
    if pages:
        for line in pages[0].text.splitlines()[:5]:
            line = line.strip()
            if line and _NAME_LINE_RE.match(line) and not find_skills(line):
                name = line
                break
    phones = [p.strip() for p in PHONE_RE.findall(text) if sum(c.isdigit() for c in p) >= 10]
    return PiiEntities(
        full_name=name,
        emails=sorted(set(EMAIL_RE.findall(text))),
        phones=sorted(set(phones)),
        urls=sorted(set(URL_RE.findall(text))),
    )


def scrub(text: str, pii: PiiEntities) -> str:
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = URL_RE.sub("[URL]", text)
    text = PHONE_RE.sub(lambda m: "[PHONE]" if sum(c.isdigit() for c in m.group(0)) >= 10 else m.group(0), text)
    if pii.full_name:
        text = re.sub(re.escape(pii.full_name), "[NAME]", text, flags=re.IGNORECASE)
        for part in pii.full_name.split():
            if len(part) > 2:
                text = re.sub(rf"\b{re.escape(part)}\b", "[NAME]", text)
    return text


# ------------------------------------------------------------------ chunking + profile
SECTION_WORDS = {
    "summary": "summary", "profile": "summary", "objective": "summary",
    "experience": "experience", "work experience": "experience", "employment": "experience",
    "skills": "skills", "technical skills": "skills", "education": "education",
    "projects": "projects", "certifications": "certifications",
}


def _section_of(line: str) -> str | None:
    key = line.strip().strip(":").lower()
    return SECTION_WORDS.get(key)


def chunk_pages(pages: list[Page], pii: PiiEntities, max_chars: int = 1200) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        text = page.text
        section = "header" if page.page_no == 1 else "other"
        # block boundaries = line starts of section headings + blank lines
        starts: list[tuple[int, str]] = [(0, section)]
        pos = 0
        for line in text.splitlines(keepends=True):
            sec = _section_of(line)
            if sec:
                starts.append((pos, sec))
            pos += len(line)
        starts.append((len(text), ""))
        for (s, sec), (e, _) in zip(starts, starts[1:]):
            while s < e:
                end = min(e, s + max_chars)
                if end < e:  # try to cut at a newline
                    nl = text.rfind("\n", s + 1, end)
                    if nl > s:
                        end = nl + 1
                original = text[s:end]
                if original.strip():
                    scrubbed = scrub(original, pii)
                    chunks.append(Chunk(
                        chunk_index=len(chunks), section=sec, page_no=page.page_no,
                        char_start=s, char_end=end, text_original=original,
                        text_scrubbed=scrubbed, token_count=len(scrubbed.split()),
                    ))
                s = end
    return chunks


_YEARS_RE = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)", re.IGNORECASE)
_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-–to]+\s*((?:19|20)\d{2}|present|current)\b", re.IGNORECASE)


def parse_profile(pages: list[Page], chunks: list[Chunk]) -> ResumeProfile:
    text = "\n".join(c.text_scrubbed for c in chunks)
    years = [int(y) for y in _YEARS_RE.findall(text)]
    span = 0
    for a, b in _RANGE_RE.findall(text):
        end = 2026 if not b.isdigit() else int(b)
        span += max(0, end - int(a))
    yrs = float(min(40, max(years + [span, 0])))
    low = text.lower()
    edu = 0
    if re.search(r"\bph\.?d\b|doctorate", low):
        edu = 4
    elif re.search(r"\bm\.?tech\b|\bm\.?sc\b|\bmba\b|master", low):
        edu = 3
    elif re.search(r"\bb\.?tech\b|\bb\.?e\.?\b|\bb\.?sc\b|bachelor", low):
        edu = 2
    elif "diploma" in low:
        edu = 1
    titles = sorted(set(re.findall(
        r"\b((?:senior |junior |lead )?(?:software|backend|frontend|data|ml|machine learning|devops|full stack)"
        r" (?:engineer|developer|scientist|analyst))\b", low)))
    summary_chunks = [c.text_scrubbed for c in chunks if c.section == "summary"]
    return ResumeProfile(
        skills=find_skills(text), titles=titles, years_experience=yrs, education_level=edu,
        summary=(summary_chunks[0][:300].strip() if summary_chunks else None),
        parser_version=PIPELINE_VERSION,
    )


def run_ingest(pdf_bytes: bytes, filename: str = "") -> tuple[ExtractedDoc, IngestResult]:
    doc = extract_pdf(pdf_bytes)
    if doc.is_scanned:
        return doc, IngestResult(pii=PiiEntities(), chunks=[], profile=ResumeProfile(parser_version=PIPELINE_VERSION))
    pii = detect_pii(doc.pages)
    chunks = chunk_pages(doc.pages, pii)
    return doc, IngestResult(pii=pii, chunks=chunks, profile=parse_profile(doc.pages, chunks))


# ------------------------------------------------------------------ LLM + JD
class StubLLM:
    name = "stub-llm"


def get_llm_client() -> StubLLM:
    return StubLLM()


def parse_job_description(text: str, llm: Any = None) -> JobSpec:
    required: list[str] = []
    preferred: list[str] = []
    mode = "required"
    resp: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("preferred", "nice to have", "bonus", "plus")):
            mode = "preferred"
        elif any(k in low for k in ("required", "must have", "requirements", "must")):
            mode = "required"
        skills = find_skills(line)
        (preferred if mode == "preferred" else required).extend(skills)
        if line.strip().startswith(("-", "*", "•")) and not skills:
            resp.append(line.strip(" -*•\t"))
    required = list(dict.fromkeys(required))
    preferred = [s for s in dict.fromkeys(preferred) if s not in required]
    m = _YEARS_RE.search(text)
    low = text.lower()
    edu = 4 if "phd" in low else 3 if "master" in low else 2 if ("bachelor" in low or "b.tech" in low or "degree" in low) else None
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    return JobSpec(
        title=(first[:120] if first else None), raw_text=text, required_skills=required,
        preferred_skills=preferred, min_years=float(m.group(1)) if m else None,
        min_education=edu, responsibilities=resp[:10],
    )


def build_queries(job: JobSpec) -> Queries:
    dense = " ".join(filter(None, [job.title or "", *job.required_skills, *job.preferred_skills, *job.responsibilities]))
    terms: list[str] = []
    for s in [*job.required_skills, *job.preferred_skills]:
        for w in re.findall(r"[a-z0-9_]+", s.lower()):
            if w not in terms:
                terms.append(w)
    return Queries(dense_query=dense or job.raw_text[:500], ts_query=" | ".join(terms[:25]))


# ------------------------------------------------------------------ reranker
class StubReranker:
    version = "stub-linear-v1"
    weights = {"required_skill_coverage": 55.0, "preferred_skill_coverage": 15.0,
               "years_fit": 15.0, "edu_ok": 5.0, "retrieval": 10.0}

    def rank(self, job: JobSpec, candidates: list[CandidateEvidence]) -> list[RankedCandidate]:
        req = {canonical_skill(s) for s in job.required_skills}
        pref = {canonical_skill(s) for s in job.preferred_skills}
        max_rrf = max((c.rrf_sum_top3 for c in candidates), default=0.0) or 1.0
        scored = []
        for c in candidates:
            skills = {canonical_skill(s) for s in c.profile.skills}
            fv = {
                "required_skill_coverage": len(req & skills) / len(req) if req else 1.0,
                "preferred_skill_coverage": len(pref & skills) / len(pref) if pref else 1.0,
                "years_fit": min(1.0, c.profile.years_experience / job.min_years) if job.min_years else 1.0,
                "edu_ok": 1.0 if (job.min_education is None or c.profile.education_level >= job.min_education) else 0.0,
                "retrieval": c.rrf_sum_top3 / max_rrf,
            }
            contrib = {k: round(self.weights[k] * v, 3) for k, v in fv.items()}
            scored.append((sum(contrib.values()), c, fv, contrib))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            RankedCandidate(candidate_id=c.candidate_id, match_score=round(min(100.0, max(0.0, s)), 2),
                            rank=i + 1, feature_values=fv, feature_contributions=contrib)
            for i, (s, c, fv, contrib) in enumerate(scored)
        ]


def get_reranker() -> StubReranker:
    return StubReranker()


# ------------------------------------------------------------------ skill gap
def _quote_for(text: str, skill: str) -> str | None:
    pat = re.compile(rf"(?<![\w+#.]){re.escape(skill)}(?![\w+#])", re.IGNORECASE)
    m = pat.search(text)
    if not m:
        for alias, canon in _ALIAS_TO_CANON.items():
            if canon == skill and alias != skill:
                m = re.search(rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])", text, re.IGNORECASE)
                if m:
                    break
    if not m:
        return None
    # the line containing the match, trimmed to <= 25 words around it
    ls = text.rfind("\n", 0, m.start()) + 1
    le = text.find("\n", m.end())
    line = text[ls: le if le != -1 else len(text)].strip()
    words = line.split()
    if len(words) > 25:
        line = " ".join(words[:25])
        if line not in text:
            return text[m.start(): m.end()]
    return line


def analyze_skill_gap(job: JobSpec, profile: ResumeProfile, chunks: list[RetrievedChunk], llm: Any = None) -> SkillGapResult:
    out: list[SkillAssessment] = []
    for skill in [*job.required_skills, *job.preferred_skills]:
        canon = canonical_skill(skill)
        cits: list[Citation] = []
        strong = False
        for ch in chunks:
            q = _quote_for(ch.text_scrubbed, canon)
            if q:
                cits.append(Citation(chunk_id=ch.chunk_id, quote=q))
                strong = strong or ch.section in ("experience", "projects")
            if len(cits) >= 2:
                break
        status = "present" if cits and strong else "partial" if cits else "missing"
        reason = {"present": "Used in work experience or projects.",
                  "partial": "Listed, but not shown in experience.",
                  "missing": "No mention found in the resume."}[status]
        out.append(SkillAssessment(skill=canon, status=status, reasoning=reason, citations=cits))
    req = {canonical_skill(s) for s in job.required_skills}
    missing = [s.skill for s in out if s.status == "missing" and s.skill in req]
    present = [s.skill for s in out if s.status == "present"]
    return SkillGapResult(
        summary=f"{len(present)} skills evidenced in experience; {len(missing)} required skills missing.",
        skills=out,
        strengths=[f"Hands-on {s}" for s in present[:3]],
        risks=[f"No evidence of {s}" for s in missing[:3]],
        interview_questions=[f"Describe a project where you used {s}." for s in (present + missing)[:4]]
        or ["Walk me through your most recent project."],
        dropped_unverified_claims=0, model="stub", prompt_version=PROMPT_VERSION,
    )
