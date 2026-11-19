#!/usr/bin/env python3
"""Generate the API fixtures (the frontend's side of the contract).

Writes the same files to docs/contracts/fixtures/ (canonical, validated against the Pydantic API models by
tests/test_fixtures_match_models.py) and frontend/fixtures/ (copy used by `?mock=1` mode and by mock_server/).

Everything is fictional and deterministic. Re-run after any intentional contract change:
    python scripts/make_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIRS = [ROOT / "docs/contracts/fixtures", ROOT / "frontend/fixtures"]
NS = uuid.UUID("6f1d2c3e-8b0a-4f55-9a57-0d6a1d0c7e11")
OWNER = uuid.uuid5(NS, "demo@verihire.example")
NOW = "2026-11-07T09:30:00+00:00"

FIRST = [
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ananya",
    "Vikram",
    "Isha",
    "Arjun",
    "Nisha",
    "Sameer",
    "Tara",
    "Neil",
    "Priya",
    "Dev",
    "Lena",
    "Omar",
    "Sofia",
    "Ethan",
    "Chloe",
]
LAST = [
    "Sharma",
    "Verma",
    "Nair",
    "Iyer",
    "Patel",
    "Mehta",
    "Reddy",
    "Kapoor",
    "Das",
    "Bose",
    "Khan",
    "Rao",
    "Chen",
    "Silva",
    "Novak",
    "Mori",
    "Garcia",
    "Haddad",
    "Okafor",
    "Larsen",
]
COMPANIES = [
    "Northwind Labs",
    "Bluepine Systems",
    "Quantum Loop",
    "Ardent Analytics",
    "Helio Works",
    "Kestrel Soft",
    "Mosaic Cloud",
    "Redwood Digital",
    "Lumen Data",
    "Orbit Commerce",
    "Tidal Software",
    "Vertex Mobility",
]
UNIS = ["KIIT University", "Pune Institute of Technology", "Greenfield University", "Riverbend College of Engineering"]
THINGS = [
    "internal tooling",
    "customer-facing services",
    "data pipelines",
    "an analytics platform",
    "a billing module",
    "an onboarding workflow",
    "a notification system",
    "reporting dashboards",
    "a search feature",
    "an admin portal",
]
DISPLAY = {
    "aws": "AWS",
    "sql": "SQL",
    "css": "CSS",
    "html": "HTML",
    "ci/cd": "CI/CD",
    "rest": "REST APIs",
    "fastapi": "FastAPI",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "graphql": "GraphQL",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "node.js": "Node.js",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "scikit-learn": "scikit-learn",
    "spring boot": "Spring Boot",
    "junit": "JUnit",
    "machine learning": "machine learning",
    "xgboost": "XGBoost",
}
ALIASES = {
    "rest": ["REST APIs", "REST"],
    "postgresql": ["PostgreSQL", "Postgres"],
    "aws": ["AWS"],
    "ci/cd": ["CI/CD"],
    "node.js": ["Node.js"],
    "spring boot": ["Spring Boot"],
}

JD_TEXT = """Backend Engineer (Python)
We are hiring a backend engineer to build and scale our document intelligence APIs.
- Design REST APIs and background workers
- Own PostgreSQL schemas and query performance
- Containerise and deploy services
Requirements: 3+ years of experience. Strong Python, FastAPI or Django, PostgreSQL, Docker and REST.
Preferred: Redis, AWS, Kafka. Bachelor degree in computer science."""
REQUIRED = ["python", "fastapi", "postgresql", "docker", "rest"]
PREFERRED = ["redis", "aws", "kafka"]
MIN_YEARS, MIN_EDU = 3.0, 2

# (role, title, years, edu, skills)
PEOPLE = [
    ("backend", "Senior Backend Engineer", 7, 3, ["python", "fastapi", "postgresql", "docker", "rest", "redis", "aws", "git"]),
    ("backend", "Backend Engineer", 5, 2, ["python", "django", "postgresql", "docker", "rest", "redis", "git"]),
    ("backend", "Python Developer", 4, 2, ["python", "fastapi", "postgresql", "rest", "git", "kafka"]),
    ("backend", "Software Engineer", 3, 2, ["python", "flask", "mysql", "docker", "rest", "git"]),
    ("java", "Java Developer", 6, 2, ["java", "spring boot", "mysql", "microservices", "rest", "docker", "kafka"]),
    ("data", "Data Scientist", 4, 3, ["python", "pandas", "numpy", "scikit-learn", "sql", "postgresql", "docker"]),
    ("devops", "DevOps Engineer", 5, 2, ["docker", "kubernetes", "aws", "terraform", "linux", "ci/cd", "python"]),
    ("frontend", "Frontend Developer", 3, 2, ["javascript", "typescript", "react", "html", "css", "node.js", "git"]),
    ("backend", "Principal Engineer", 9, 2, ["python", "django", "fastapi", "postgresql", "docker", "aws", "kafka", "redis", "rest"]),
    ("backend", "Junior Backend Developer", 2, 2, ["python", "fastapi", "postgresql", "git"]),
    ("data", "ML Engineer", 6, 4, ["python", "pytorch", "tensorflow", "sql", "machine learning", "airflow"]),
    ("qa", "QA Engineer", 4, 2, ["selenium", "pytest", "java", "jira", "agile", "python"]),
    ("frontend", "UI Engineer", 5, 3, ["javascript", "react", "typescript", "graphql", "node.js", "css"]),
    (
        "devops",
        "Site Reliability Engineer",
        7,
        3,
        ["kubernetes", "terraform", "aws", "docker", "ansible", "linux", "prometheus", "grafana"],
    ),
    ("backend", "Backend Developer", 3, 2, ["python", "django", "mysql", "rest", "docker"]),
    ("data", "Data Analyst", 2, 2, ["python", "sql", "pandas", "tableau", "excel"]),
    ("backend", "API Engineer", 6, 3, ["python", "fastapi", "postgresql", "docker", "rest", "aws", "redis"]),
    ("frontend", "Web Developer", 2, 2, ["html", "css", "javascript", "react", "git"]),
    ("devops", "Cloud Engineer", 4, 2, ["aws", "docker", "terraform", "linux", "python", "ci/cd"]),
    ("java", "Backend Engineer", 8, 3, ["java", "spring boot", "postgresql", "microservices", "rest", "kafka", "docker"]),
]


def uid(key: str) -> str:
    return str(uuid.uuid5(NS, key))


def disp(skill: str) -> str:
    return DISPLAY.get(skill, skill.capitalize())


def label(cid: str) -> str:
    return "Candidate #" + hashlib.sha1(uuid.UUID(cid).bytes).hexdigest()[:6].upper()


def skill_pattern(skill: str) -> re.Pattern[str]:
    names = ALIASES.get(skill, [disp(skill), skill])
    alts = "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))
    return re.compile(rf"(?<![\w+#.])(?:{alts})(?![\w+#])", re.IGNORECASE)


def build_person(i: int) -> dict:
    role, title, years, edu, skills = PEOPLE[i]
    rnd = random.Random(1000 + i)
    first, last = FIRST[i % len(FIRST)], LAST[(i * 7 + 3) % len(LAST)]
    cid = uid(f"cand-{i}")
    did = uid(f"doc-{i}")
    comp = rnd.sample(COMPANIES, 2)
    end = 2026
    split = end - max(1, years // 2)
    start = end - years

    def bullets(sk: list[str]) -> str:
        return "\n".join(f"- Built and maintained {rnd.choice(THINGS)} using {disp(s)}, serving {rnd.randint(2, 90)}k users." for s in sk)

    half = max(2, len(skills) // 2)
    base = 1000 + i * 10
    cid_of = lambda n: uid(f"chunk-{n}")  # noqa: E731  - real chunk ids are UUID strings (v1.3 integration)
    chunks = [
        dict(chunk_id=cid_of(base), section="other", page_no=1, text=f"[NAME]\n{title}\n[EMAIL] | [PHONE] | [URL]"),
        dict(
            chunk_id=cid_of(base + 1),
            section="summary",
            page_no=1,
            text=f"{title} with {years} years of experience delivering production systems with "
            f"{', '.join(disp(s) for s in skills[:3])}. Focused on reliable, well-tested software.",
        ),
        dict(
            chunk_id=cid_of(base + 2),
            section="experience",
            page_no=1,
            text=f"{title} - {comp[0]}   {split} - Present\n{bullets(skills[:half])}",
        ),
    ]
    nxt = 3
    if years >= 2 and skills[half:]:
        chunks.append(
            dict(
                chunk_id=cid_of(base + nxt),
                section="experience",
                page_no=1,
                text=f"Associate {title} - {comp[1]}   {start} - {split}\n{bullets(skills[half:][:3])}",
            )
        )
        nxt += 1
    chunks.append(
        dict(chunk_id=cid_of(base + nxt), section="skills", page_no=2 if years > 6 else 1, text=", ".join(disp(s) for s in skills))
    )
    chunks.append(
        dict(
            chunk_id=cid_of(base + nxt + 1),
            section="education",
            page_no=2 if years > 6 else 1,
            text=f"{['', 'Diploma', 'B.Tech in Computer Science', 'M.Tech in Computer Science', 'PhD in Computer Science'][edu]}, "
            f"{rnd.choice(UNIS)}, {start - 4}",
        )
    )
    return dict(
        i=i,
        role=role,
        title=title,
        years=float(years),
        edu=edu,
        skills=skills,
        cid=cid,
        did=did,
        name=f"{first} {last}",
        email=f"{first.lower()}.{last.lower()}@example.com",
        phone=f"+91 {rnd.randint(70000, 99999)} {rnd.randint(10000, 99999)}",
        chunks=chunks,
    )


def profile(p: dict) -> dict:
    return {
        "skills": p["skills"],
        "titles": [p["title"]],
        "years_experience": p["years"],
        "education_level": p["edu"],
        "education_fields": ["computer science"] if p["edu"] else [],
        "certifications": [],
        "summary": p["chunks"][1]["text"],
        "parser_version": "fixture-1",
    }


def score_candidate(p: dict) -> dict:
    have = set(p["skills"])
    cov = len([s for s in REQUIRED if s in have]) / len(REQUIRED)
    pref = len([s for s in PREFERRED if s in have]) / len(PREFERRED)
    proxy = (cov * len(REQUIRED) + 0.5 * pref * len(PREFERRED)) / (len(REQUIRED) + 0.5 * len(PREFERRED))
    exp_chunks = [c for c in p["chunks"] if c["section"] == "experience"]
    in_exp = [s for s in p["skills"] if any(skill_pattern(s).search(c["text"]) for c in exp_chunks)]
    in_exp_ratio = len(in_exp) / max(1, len(p["skills"]))
    values = {
        "required_skill_coverage": cov,
        "rrf_sum_top3": round(0.098 * proxy * 0.92, 4),
        "skills_in_experience_ratio": in_exp_ratio,
        "preferred_skill_coverage": pref,
        "years_gap": min(1.0, max(0.0, 0.5 + (p["years"] - MIN_YEARS) / 10)),
        "experience_section_hit": 1.0 if cov > 0 else 0.0,
        "title_match": 1.0
        if ("Backend" in p["title"] or "Python" in p["title"] or "API" in p["title"])
        else (0.6 if "Software" in p["title"] or "Principal" in p["title"] else 0.2),
        "edu_gap": 1.0 if p["edu"] >= MIN_EDU else 0.25,
    }
    weights = {
        "required_skill_coverage": 0.30,
        "rrf_sum_top3": 0.15,
        "skills_in_experience_ratio": 0.15,
        "preferred_skill_coverage": 0.10,
        "years_gap": 0.10,
        "experience_section_hit": 0.08,
        "title_match": 0.07,
        "edu_gap": 0.05,
    }
    norm = {k: (v / 0.098 if k == "rrf_sum_top3" else v) for k, v in values.items()}
    contrib = {k: round(weights[k] * (norm[k] - 0.5) * 100, 2) for k in weights}
    score = round(sum(weights[k] * norm[k] for k in weights) * 100, 1)
    return {"score": score, "values": {k: round(v, 4) for k, v in values.items()}, "contrib": contrib, "proxy": proxy}


def sentence_with(skill: str, text: str) -> tuple[str, int, int] | None:
    m = skill_pattern(skill).search(text)
    if not m:
        return None
    ls = text.rfind("\n", 0, m.start()) + 1
    le = text.find("\n", m.end())
    le = len(text) if le == -1 else le
    line = text[ls:le]
    stripped = line.lstrip("- ")
    ls += len(line) - len(stripped)
    return text[ls:le], ls, le


def analysis(p: dict) -> dict:
    skills_out, resolved = [], []
    for skill in REQUIRED + PREFERRED:
        cites = []
        strong = False
        for ch in p["chunks"]:
            hit = sentence_with(skill, ch["text"])
            if not hit:
                continue
            quote, s, e = hit
            if len(quote.split()) > 25:
                words = list(re.finditer(r"\S+", quote))
                quote = quote[: words[24].end()]
                e = s + len(quote)
            cites.append({"chunk_id": ch["chunk_id"], "quote": quote})
            resolved.append(
                {
                    "skill": skill,
                    "chunk_id": ch["chunk_id"],
                    "quote": quote,
                    "text_scrubbed": ch["text"],
                    "page_no": ch["page_no"],
                    "section": ch["section"],
                    "start": s,
                    "end": e,
                }
            )
            strong = strong or ch["section"] == "experience"
            if len(cites) == 2:
                break
        if not cites:
            skills_out.append({"skill": skill, "status": "missing", "reasoning": "No mention found in the resume.", "citations": []})
        elif strong:
            skills_out.append(
                {"skill": skill, "status": "present", "reasoning": "Used in work experience with concrete scope.", "citations": cites}
            )
        else:
            skills_out.append(
                {"skill": skill, "status": "partial", "reasoning": "Listed, but no work context shows how it was used.", "citations": cites}
            )
    req_present = [s for s in skills_out if s["skill"] in REQUIRED and s["status"] == "present"]
    req_missing = [s for s in skills_out if s["skill"] in REQUIRED and s["status"] == "missing"]
    result = {
        "summary": f"{len(req_present)} of {len(REQUIRED)} required skills are shown in work experience"
        + (f"; {len(req_missing)} have no evidence." if req_missing else "."),
        "skills": skills_out,
        "strengths": [f"Hands-on {disp(s['skill'])} in production work." for s in req_present[:3]],
        "risks": [f"No evidence of {disp(s['skill'])}." for s in req_missing[:3]]
        + ([f"Only {p['years']:.0f} years of experience against a minimum of {MIN_YEARS:.0f}."] if p["years"] < MIN_YEARS else []),
        "interview_questions": [f"Describe a system you built with {disp(s['skill'])} and how you tested it." for s in req_present[:2]]
        + [f"How would you get productive with {disp(s['skill'])}?" for s in req_missing[:2]],
        "dropped_unverified_claims": 0,
        "model": "fixture-model",
        "prompt_version": "skillgap_v1",
    }
    return {"result": result, "citations_resolved": resolved, "from_cache": False}


def doc_item(
    i: int, status: str, filename: str, p: dict | None = None, error: str | None = None, pages: int | None = 1, mins_ago: int = 0
) -> dict:
    mm = 30 - mins_ago
    return {
        "id": p["did"] if p else uid(f"doc-extra-{i}"),
        "filename": filename,
        "doc_type": "resume",
        "status": status,
        "error": error,
        "error_code": None if error is None else "corrupt",
        "page_count": pages,
        "suspicious_hidden_text": bool(p and p["i"] == 11),
        "size_bytes": 41_000 + 997 * i,
        "created_at": f"2026-11-07T09:{max(0, mm):02d}:00+00:00",
        "updated_at": f"2026-11-07T09:{max(0, mm):02d}:30+00:00",
        "candidate_id": p["cid"] if p else None,
        "years_experience": p["years"] if p else None,
        "education_level": p["edu"] if p else None,
        "skills": p["skills"] if p else [],
    }


def write_sample_pdf(path: Path, p: dict) -> None:
    """A real one-page PDF so the PDF viewer modal has something to show in mock mode."""
    try:
        import pymupdf  # type: ignore
    except ImportError:
        print("pymupdf not installed: skipping sample_resume.pdf")
        return
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60.0
    page.insert_text((50, y), p["name"], fontsize=16, fontname="hebo")
    y += 22
    for line in [p["title"], f"{p['email']} | {p['phone']}", ""] + [c["text"].replace("[NAME]", p["name"]) for c in p["chunks"][1:]]:
        for ln in line.split("\n") if line else [""]:
            page.insert_text((50, y), ln[:100], fontsize=10, fontname="helv")
            y += 14
        y += 6
    doc.save(path)
    doc.close()


def main() -> None:
    people = [build_person(i) for i in range(len(PEOPLE))]
    scored = {p["cid"]: score_candidate(p) for p in people}
    ranking = sorted(people, key=lambda p: (-scored[p["cid"]]["score"], p["cid"]))

    docs = [doc_item(i, "ready", f"resume_{i + 1:03d}.pdf", p, pages=2 if p["years"] > 6 else 1, mins_ago=i) for i, p in enumerate(people)]
    docs += [
        doc_item(101, "failed", "corrupt_upload.pdf", error="The file is corrupt or not a readable PDF.", pages=None, mins_ago=2),
        doc_item(102, "failed", "locked_resume.pdf", error="The PDF is password protected.", pages=None, mins_ago=3),
        doc_item(
            103, "needs_ocr", "scan_2019_cv.pdf", error="Scanned PDF: no selectable text found (OCR is not enabled).", pages=2, mins_ago=1
        ),
        doc_item(104, "extracting", "resume_021.pdf", pages=None, mins_ago=0),
        doc_item(105, "embedding", "resume_022.pdf", pages=1, mins_ago=0),
        doc_item(106, "uploaded", "resume_023.pdf", pages=None, mins_ago=0),
    ]
    docs.sort(key=lambda d: d["created_at"], reverse=True)
    counts = {
        "all": len(docs),
        "ready": sum(d["status"] == "ready" for d in docs),
        "processing": sum(d["status"] in ("uploaded", "extracting", "scrubbing", "chunking", "parsing", "embedding") for d in docs),
        "failed": sum(d["status"] == "failed" for d in docs),
        "needs_ocr": sum(d["status"] == "needs_ocr" for d in docs),
    }
    documents = {"items": docs, "total": len(docs), "page": 1, "page_size": 50, "counts": counts}

    jd_id = uid("jd-1")
    spec = {
        "title": "Backend Engineer (Python)",
        "raw_text": JD_TEXT,
        "required_skills": REQUIRED,
        "preferred_skills": PREFERRED,
        "min_years": MIN_YEARS,
        "min_education": MIN_EDU,
        "responsibilities": [
            "Design REST APIs and background workers",
            "Own PostgreSQL schemas and query performance",
            "Containerise and deploy services",
        ],
    }
    jd = {"id": jd_id, "title": spec["title"], "spec": spec, "created_at": NOW, "updated_at": NOW}
    search_id = uid("search-1")
    fe_skills = ["javascript", "typescript", "react", "html", "css"]
    fe_spec = {
        "title": "Frontend Developer",
        "raw_text": "Frontend Developer\nRequirements: " + ", ".join(fe_skills) + ".",
        "required_skills": fe_skills,
        "preferred_skills": [],
        "min_years": None,
        "min_education": None,
        "responsibilities": [],
    }
    # backend shape (GET /job-descriptions): {"items": [JobDescription + required_skills + last_search_id]}
    jd_list = {
        "items": [
            {**jd, "required_skills": REQUIRED, "last_search_id": search_id},
            {
                "id": uid("jd-2"),
                "title": "Frontend Developer",
                "spec": fe_spec,
                "created_at": "2026-11-06T16:10:00+00:00",
                "updated_at": "2026-11-06T16:10:00+00:00",
                "required_skills": fe_skills,
                "last_search_id": None,
            },
        ]
    }

    def result_for(p: dict, rank: int) -> dict:
        sc = scored[p["cid"]]
        have = set(p["skills"])
        ranked_chunks = sorted(p["chunks"], key=lambda c: -sum(bool(skill_pattern(s).search(c["text"])) for s in REQUIRED + PREFERRED))
        return {
            "candidate_id": p["cid"],
            "document_id": p["did"],
            "label": label(p["cid"]),
            "match_score": sc["score"],
            "rank": rank,
            "years_experience": p["years"],
            "education_level": p["edu"],
            "titles": [p["title"]],
            "top_skills": p["skills"][:12],
            "matched_required": [s for s in REQUIRED if s in have],
            "missing_required": [s for s in REQUIRED if s not in have],
            "matched_preferred": [s for s in PREFERRED if s in have],
            "missing_preferred": [s for s in PREFERRED if s not in have],
            "suspicious_hidden_text": p["i"] == 11,
            "top_chunks": [
                {"chunk_id": c["chunk_id"], "page_no": c["page_no"], "section": c["section"], "text_scrubbed": c["text"]}
                for c in ranked_chunks[:3]
            ],
            "explanation": {"feature_contributions": sc["contrib"], "feature_values": sc["values"], "cross_encoder_score": None},
            "retrieval": {"rrf_sum_top3": sc["values"]["rrf_sum_top3"], "n_chunks_hit": min(len(p["chunks"]), 3 + p["i"] % 3)},
        }

    results = [result_for(p, rank) for rank, p in enumerate(ranking[:10], 1)]
    pool = [result_for(p, 0) for p in ranking]
    chunks_by_cand = {
        p["cid"]: [{"chunk_id": c["chunk_id"], "section": c["section"], "page_no": c["page_no"], "text": c["text"]} for c in p["chunks"]]
        for p in people
    }
    search = {
        "search_id": search_id,
        "jd_id": jd_id,
        "created_at": NOW,
        "blind": True,
        "results": results,
        "timings": {"query_build_ms": 4.2, "retrieval_ms": 38.5, "rerank_ms": 11.9, "total_ms": 61.3},
        "warnings": [],
        "reranker_version": "fixture-rerank-1",
        "embedder": "fixture-embedder-384",
        "job_spec": spec,
    }

    candidates = {
        p["cid"]: {
            "id": p["cid"],
            "document_id": p["did"],
            "label": label(p["cid"]),
            "years_experience": p["years"],
            "education_level": p["edu"],
            "profile": profile(p),
            "suspicious_hidden_text": p["i"] == 11,
            "pii": None,
        }
        for p in people
    }
    pii = {p["cid"]: {"full_name": p["name"], "email": p["email"], "phone": p["phone"], "other": {}} for p in people}
    analyses = {p["cid"]: analysis(p) for p in people}

    files = {
        "health.json": {
            "status": "ok",
            "db": "ok",
            "embedder": "fixture-embedder-384",
            "reranker": "fixture-rerank-1",
            "contract_version": "1.1.0",
            "ml_mode": "stub",
            "ml_components": {
                "run_ingest": "stub",
                "get_embedder": "stub",
                "get_llm_client": "stub",
                "parse_job_description": "stub",
                "build_queries": "stub",
                "get_reranker": "stub",
                "analyze_skill_gap": "stub",
            },
            "stubs_in_use": True,
            "auth_mode": "dev",
            "storage_mode": "local",
        },
        "me.json": {"id": str(OWNER), "email": "demo@verihire.example"},
        "upload_response.json": {
            "job_id": uid("job-upload"),
            "document_ids": [uid("doc-new-1"), uid("doc-new-2")],
            "rejected": [{"filename": "notes.docx", "reason": "Only PDF files are accepted."}],
        },
        "retry_response.json": {"job_id": uid("job-retry")},
        "job_running.json": {
            "id": uid("job-upload"),
            "status": "running",
            "total": 4,
            "done": 2,
            "failed": 0,
            "created_at": NOW,
            "finished_at": None,
            "documents": [
                {"id": uid("doc-new-1"), "filename": "resume_024.pdf", "status": "ready", "error": None},
                {"id": uid("doc-new-2"), "filename": "resume_025.pdf", "status": "ready", "error": None},
                {"id": uid("doc-new-3"), "filename": "resume_026.pdf", "status": "embedding", "error": None},
                {"id": uid("doc-new-4"), "filename": "resume_027.pdf", "status": "uploaded", "error": None},
            ],
        },
        "job_completed_with_errors.json": {
            "id": uid("job-upload"),
            "status": "completed_with_errors",
            "total": 3,
            "done": 2,
            "failed": 1,
            "created_at": NOW,
            "finished_at": "2026-11-07T09:31:10+00:00",
            "documents": [
                {"id": uid("doc-new-1"), "filename": "resume_024.pdf", "status": "ready", "error": None},
                {"id": uid("doc-new-2"), "filename": "resume_025.pdf", "status": "ready", "error": None},
                {
                    "id": uid("doc-new-3"),
                    "filename": "corrupt_upload.pdf",
                    "status": "failed",
                    "error": "The file is corrupt or not a readable PDF.",
                },
            ],
        },
        "documents.json": documents,
        "pdf_url.json": {"url": "/fixtures/sample_resume.pdf", "expires_in": 600},
        "jd.json": jd,
        "jd_list.json": jd_list,
        "search.json": search,
        "candidates.json": candidates,
        "pii.json": pii,
        "analyses.json": analyses,
        "pool.json": pool,
        "chunks.json": chunks_by_cand,
        "errors.json": {
            "401": {"error": {"code": "unauthorized", "message": "Missing or invalid credentials"}},
            "404": {"error": {"code": "not_found", "message": "Candidate not found"}},
            "422": {"error": {"code": "validation_error", "message": "text: String should have at least 40 characters"}},
            "429": {"error": {"code": "rate_limited", "message": "Too many requests. Try again in 12s."}},
            "500": {"error": {"code": "internal_error", "message": "Internal server error"}},
        },
    }
    for d in OUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (d / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_sample_pdf(ROOT / "frontend/fixtures/sample_resume.pdf", people[0])
    print(
        f"wrote {len(files)} fixtures x {len(OUT_DIRS)} dirs; top candidate score {results[0]['match_score']}, "
        f"bottom of top-10 {results[-1]['match_score']}"
    )


if __name__ == "__main__":
    main()
