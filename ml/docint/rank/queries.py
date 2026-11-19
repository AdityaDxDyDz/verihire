"""E3: build_queries(job) -> Queries (dense text + OR-joined sanitised ts_query)."""
from __future__ import annotations

import re

from docint.contracts import JobSpec, Queries

MAX_TERMS = 25
STOP = {"a", "an", "and", "or", "the", "of", "in", "to", "for", "with", "on", "at", "is", "are"}
# characters Postgres to_tsquery would choke on are removed by the tokenizer below;
# these token rewrites keep symbol-heavy skills searchable.
REWRITES = [("c++", "cpp"), ("c#", "csharp"), (".net", "dotnet"), ("node.js", "nodejs"),
            ("next.js", "nextjs"), ("vue.js", "vuejs"), ("react.js", "reactjs"), ("ci/cd", "cicd")]
TOKEN = re.compile(r"[a-z0-9_]+")
VALID_TSQUERY = re.compile(r"^([a-z0-9_]+( \| [a-z0-9_]+)*)?$")


def normalize_text(text: str) -> str:
    t = text.lower()
    for a, b in REWRITES:
        t = t.replace(a, b)
    return t


def tokenize(text: str) -> list[str]:
    """Same tokenizer for documents (BM25) and queries so terms line up."""
    return [w for w in TOKEN.findall(normalize_text(text)) if w not in STOP]


def build_ts_query(job: JobSpec) -> str:
    terms: list[str] = []
    for skill in list(job.required_skills) + list(job.preferred_skills):  # required first
        for w in tokenize(skill):
            if w not in terms:
                terms.append(w)
            if len(terms) >= MAX_TERMS:
                break
        if len(terms) >= MAX_TERMS:
            break
    return " | ".join(terms)


def build_dense_query(job: JobSpec) -> str:
    parts = []
    if job.title:
        parts.append(job.title)
    if job.required_skills:
        parts.append("Required skills: " + ", ".join(job.required_skills))
    if job.preferred_skills:
        parts.append("Preferred skills: " + ", ".join(job.preferred_skills))
    if job.responsibilities:
        parts.append("Responsibilities: " + "; ".join(job.responsibilities[:4]))
    return ". ".join(parts) if parts else job.raw_text[:500]


def build_queries(job: JobSpec) -> Queries:
    q = Queries(dense_query=build_dense_query(job), ts_query=build_ts_query(job))
    assert VALID_TSQUERY.match(q.ts_query), q.ts_query  # contract: always valid for to_tsquery
    return q
