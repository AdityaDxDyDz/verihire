"""Dependency-free generator of small, valid, *fictional* resume PDFs with known ground truth.

Used by backend tests, ``scripts/seed_demo.py`` and ``scripts/smoke_test.py``.
Not imported by the running app. (``scripts/synth.py`` from the Foundation PR is the
team-wide generator; this one exists so backend tests never depend on it.)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], hidden_lines: list[str] | None = None, encrypted: bool = False) -> bytes:
    """Build a PDF where each page is a list of text lines (Helvetica 10pt).

    ``hidden_lines`` are drawn in white 1pt text (prompt-injection tests).
    ``encrypted`` adds an /Encrypt entry (simulates a password-protected file).
    """
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id_placeholder = len(objs) + 1
    add(b"")  # pages object filled later
    page_ids = []
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 10 Tf", "12 TL", "50 780 Td"]
        for ln in lines:
            ops.append(f"({_esc(ln)}) Tj T*")
        ops.append("ET")
        if hidden_lines and i == 0:
            ops += ["BT", "1 1 1 rg", "/F1 1 Tf", "50 20 Td"] + [f"({_esc(h)}) Tj T*" for h in hidden_lines] + ["ET"]
        stream = "\n".join(ops).encode("latin-1", "replace")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 %d 0 R >> >> "
            b"/Contents %d 0 R >>" % (pages_id_placeholder, font, content)))
    kids = b" ".join(b"%d 0 R" % p for p in page_ids)
    objs[pages_id_placeholder - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id_placeholder)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    enc = b" /Encrypt << /Filter /Standard /V 1 /R 2 >>" if encrypted else b""
    out += b"trailer\n<< /Size %d /Root %d 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, enc, xref)
    return bytes(out)


FIRST = ["Asha", "Rohan", "Meera", "Kabir", "Isha", "Vikram", "Neha", "Arjun", "Tara", "Dev", "Lina", "Omar",
         "Sara", "Yusuf", "Priya", "Nikhil", "Zara", "Kunal", "Riya", "Aman"]
LAST = ["Verma", "Iyer", "Nair", "Mehta", "Rao", "Kapoor", "Bose", "Joshi", "Sethi", "Pillai", "Gill", "Das"]

ROLES: dict[str, dict] = {
    "backend": {"title": "Backend Engineer", "skills": ["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "Redis", "AWS"]},
    "frontend": {"title": "Frontend Developer", "skills": ["JavaScript", "TypeScript", "React", "HTML", "CSS", "Figma"]},
    "data": {"title": "Data Scientist", "skills": ["Python", "Pandas", "NumPy", "scikit-learn", "XGBoost", "SQL", "Tableau"]},
    "ml": {"title": "Machine Learning Engineer", "skills": ["Python", "PyTorch", "TensorFlow", "NLP", "Docker", "Kubernetes"]},
    "devops": {"title": "DevOps Engineer", "skills": ["Docker", "Kubernetes", "Terraform", "AWS", "Linux", "CI/CD", "Git"]},
    "java": {"title": "Software Engineer", "skills": ["Java", "Spring", "MySQL", "Microservices", "Kafka", "Git"]},
}


@dataclass
class FakeResume:
    name: str
    email: str
    phone: str
    role: str
    skills: list[str]
    years: int
    education: int  # 2 bachelor, 3 master, 4 phd
    pdf: bytes = field(repr=False, default=b"")


def make_resume(seed: int, role: str | None = None, skills: list[str] | None = None, years: int | None = None,
                hidden: list[str] | None = None, extra_lines: list[str] | None = None) -> FakeResume:
    rng = random.Random(seed)
    role = role or rng.choice(sorted(ROLES))
    spec = ROLES[role]
    skills = skills if skills is not None else rng.sample(spec["skills"], k=min(len(spec["skills"]), rng.randint(3, 5)))
    years = years if years is not None else rng.randint(1, 12)
    first, last = rng.choice(FIRST), rng.choice(LAST)
    name = f"{first} {last}"
    email = f"{first.lower()}.{last.lower()}{seed}@example.com"
    phone = f"+91 98{rng.randint(10000000, 99999999)}"
    edu = rng.choice([2, 2, 3, 3, 4])
    degree = {2: "B.Tech in Computer Science", 3: "M.Tech in Computer Science", 4: "PhD in Computer Science"}[edu]
    start = 2026 - years
    page1 = [
        name,
        f"Email: {email} | Phone: {phone} | linkedin.com/in/{first.lower()}{seed}",
        "",
        "Summary",
        f"{spec['title']} with {years} years of experience building production systems.",
        "",
        "Experience",
        f"{spec['title']}, Fictional Corp ({start} - present)",
        f"- Built and maintained services using {', '.join(skills[:2])}.",  # bullets name skills[0], [1] and [-1]
        f"- Improved reliability and shipped features with {skills[-1]}.",
        *(extra_lines or []),
        "",
        "Skills",
        ", ".join(skills),
        "",
        "Education",
        f"{degree}, Example Institute of Technology",
    ]
    pdf = make_pdf([page1], hidden_lines=hidden)
    return FakeResume(name=name, email=email, phone=phone, role=role, skills=skills, years=years, education=edu, pdf=pdf)


def blank_pdf() -> bytes:
    return make_pdf([[""]])


def encrypted_pdf() -> bytes:
    """A really password-protected PDF when PyMuPDF is installed, else a PDF flagged with /Encrypt."""
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        return make_pdf([["Secret resume text that is long enough to be read as a normal page of text."]], encrypted=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 72), "Secret resume text that is long enough to be read as a normal page.")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    doc.close()
    return data


def corrupt_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj << garbage"


JD_BACKEND = """Senior Python Backend Developer
We are hiring a backend developer to build APIs.
Requirements: 3+ years of experience with Python, FastAPI and PostgreSQL.
Nice to have: Docker, AWS.
- Design and build REST services
- Bachelor degree in computer science
"""
