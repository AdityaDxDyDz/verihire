"""Skill dictionary access + matcher (Track C side) - the ONE skills layer for ranking and the LLM layer.

The frozen rule says ONE skills dictionary exists: ml/docint/ingest/skills_dict.json (Harsh).
INTEGRATION (v1.2): Harsh's file wraps the mapping as {"schema_version", ..., "skills": {canonical: [aliases]}};
the old loader iterated the wrapper, crashed on schema_version and silently fell back to FALLBACK_SKILLS,
so JD skills and resume skills used different names ("kafka" vs "apache kafka"). Now:
  * load_skills() unwraps "skills" and uses Harsh's 411-skill dictionary;
  * find_skills() delegates to Harsh's extract_skills() (same context rules as the resume profiles);
  * canonicalize() maps any alias / legacy Track C name to Harsh's canonical name;
  * IMPLIES is written in readable names and canonicalised on load.
FALLBACK_SKILLS + the regex matcher are used only when Track B is not installed.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

FALLBACK_SKILLS: dict[str, list[str]] = {
    # backend
    "python": ["py", "python3"], "django": [], "fastapi": ["fast api"], "flask": [],
    "java": [], "spring": ["spring boot", "spring framework", "spring mvc"],
    "postgresql": ["postgres"], "mysql": [], "sql": [], "redis": [],
    "rest api": ["rest apis", "restful api", "restful apis", "restful"],
    "docker": [], "kubernetes": ["k8s"], "aws": ["amazon web services"],
    "microservices": ["microservice"], "git": [], "kafka": [], "graphql": [],
    "mongodb": ["mongo"], "go": ["golang"], "c#": ["csharp"], ".net": ["dotnet"],
    "c++": ["cpp"],
    # frontend
    "javascript": ["js", "ecmascript"], "typescript": [], "react": ["reactjs", "react.js"],
    "angular": ["angularjs"], "vue": ["vuejs", "vue.js"], "html": ["html5"], "css": ["css3"],
    "redux": [], "webpack": [], "node.js": ["nodejs"], "express": ["express.js", "expressjs"],
    "tailwind": ["tailwindcss"], "next.js": ["nextjs"],
    # data science / ML
    "pandas": [], "numpy": [], "scikit-learn": ["sklearn", "scikit learn"],
    "tensorflow": [], "pytorch": ["torch"], "machine learning": ["ml"],
    "deep learning": [], "nlp": ["natural language processing"], "xgboost": [],
    "statistics": ["statistical analysis"], "jupyter": [], "computer vision": [],
    "data visualization": ["tableau", "power bi"],
    # data engineering
    "spark": ["pyspark", "apache spark"], "airflow": ["apache airflow"], "snowflake": [],
    "etl": [], "dbt": [], "hadoop": [], "data warehousing": ["data warehouse"],
    # devops
    "terraform": [], "jenkins": [], "ansible": [], "linux": [], "ci/cd": ["cicd", "continuous integration"],
    "prometheus": [], "grafana": [], "bash": ["shell scripting"], "azure": [], "gcp": ["google cloud"],
    # mobile
    "kotlin": [], "swift": [], "android": [], "ios": [], "flutter": [], "dart": [],
    "react native": [], "firebase": [],
}

# canonical names that are common English words: do NOT match the bare word, only aliases
NO_SELF_MATCH = {"go", "spring", "express"}

# "knowing X implies knowing Y" (used for coverage; keyword baselines do NOT get this)
IMPLIES: dict[str, list[str]] = {
    "django": ["python"], "fastapi": ["python"], "flask": ["python"], "pandas": ["python"],
    "numpy": ["python"], "scikit-learn": ["python"], "pytorch": ["python"],
    "tensorflow": ["python"], "xgboost": ["python"], "spring": ["java"],
    "react": ["javascript"], "vue": ["javascript"], "node.js": ["javascript"],
    "express": ["javascript", "node.js"], "redux": ["javascript"], "next.js": ["react", "javascript"],
    "angular": ["typescript", "javascript"], "typescript": ["javascript"],
    "spark": ["python"], "kubernetes": ["docker"], "react native": ["react", "javascript"],
    "flutter": ["dart"], "postgresql": ["sql"], "mysql": ["sql"],
}


# old Track C names that are not aliases in Harsh's dictionary (Harsh does not match bare "spring": it is a season)
LEGACY_NAMES = {"spring": "spring boot"}


def _track_b_extract():
    try:
        from docint.ingest.skills import extract_skills  # Harsh, Track B
        return extract_skills
    except Exception:  # noqa: BLE001 - Track B not installed: use the local regex matcher
        return None


@lru_cache(maxsize=1)
def load_skills() -> dict[str, list[str]]:
    p = Path(__file__).resolve().parents[1] / "ingest" / "skills_dict.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("skills"), dict):
                data = data["skills"]  # Harsh's wrapped format
            if isinstance(data, dict) and data and all(isinstance(v, list) for v in data.values()):
                return {k.lower(): [str(a).lower() for a in v] for k, v in data.items()}
            raise ValueError("unexpected skills_dict.json shape")
        except Exception as e:  # noqa: BLE001
            import warnings
            warnings.warn(f"skills_dict.json unusable ({e}); using FALLBACK_SKILLS", stacklevel=2)
    return FALLBACK_SKILLS


def using_track_b_dictionary() -> bool:
    return load_skills() is not FALLBACK_SKILLS


@lru_cache(maxsize=1)
def _alias_index() -> tuple[dict[str, str], list[tuple[re.Pattern, str]]]:
    skills = load_skills()
    alias_to_canon: dict[str, str] = {}
    for canon, aliases in skills.items():
        if canon not in NO_SELF_MATCH:
            alias_to_canon[canon] = canon
        for a in aliases:
            alias_to_canon[a] = canon
    pats = []
    for alias in sorted(alias_to_canon, key=len, reverse=True):  # longest first
        pat = re.compile(r"(?<![\w+#.\-])" + re.escape(alias) + r"(?![\w+#]|\.\w)", re.IGNORECASE)
        pats.append((pat, alias_to_canon[alias]))
    return alias_to_canon, pats


def _norm(name: str) -> str:
    return " ".join(name.strip().lower().replace("-", " ").split())


@lru_cache(maxsize=4096)
def canonicalize(name: str) -> str | None:
    """Map a skill string (canonical, alias or legacy Track C name) to its canonical form, or None if unknown."""
    n = name.strip().lower()
    if not n:
        return None
    skills = load_skills()
    alias_to_canon, _ = _alias_index()
    for cand in (n, _norm(n)):
        if cand in skills:
            return cand
        if cand in alias_to_canon:
            return alias_to_canon[cand]
        if cand in LEGACY_NAMES:
            return canonicalize(LEGACY_NAMES[cand]) if LEGACY_NAMES[cand] != cand else cand
    normed = {_norm(k): k for k in skills}
    if _norm(n) in normed:
        return normed[_norm(n)]
    return None


def find_skills(text: str) -> list[str]:
    """Canonical skills mentioned in text, in first-seen order (Track B rules when installed)."""
    extract = _track_b_extract() if using_track_b_dictionary() else None
    if extract is not None:
        out: list[str] = []
        for sk in extract(text):
            c = canonicalize(sk) or sk.lower()
            if c not in out:
                out.append(c)
        return out
    _, pats = _alias_index()
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for pat, canon in pats:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            taken.append((s, e))
            found.append((s, canon))
    found.sort()
    out: list[str] = []
    for _, c in found:
        if c not in out:
            out.append(c)
    return out


@lru_cache(maxsize=1)
def _implies_canonical() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, vs in IMPLIES.items():
        ck = canonicalize(k) or k
        out.setdefault(ck, [])
        for v in vs:
            cv = canonicalize(v) or v
            if cv not in out[ck]:
                out[ck].append(cv)
    return out


def expand_implied(skills: list[str] | set[str]) -> set[str]:
    """Add implied skills (django => python). Input names are canonicalised first."""
    imp = _implies_canonical()
    out = {canonicalize(s) or s.lower() for s in skills}
    changed = True
    while changed:
        changed = False
        for s in list(out):
            for t in imp.get(s, []):
                if t not in out:
                    out.add(t)
                    changed = True
    return out
