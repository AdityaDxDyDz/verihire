"""Job descriptions, search, analysis, candidates, feedback, CSV, blind mode, citation safety."""

from __future__ import annotations

import csv
import io
import json
import re
import time

import pytest

from app.adapters.ml import MLAdapter
from conftest import A, B, db_conn, drain, seed, upload
from devtools import synthpdf as s
from docint.contracts import Citation, SkillAssessment

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"\+\d{1,3}[\s-]?\d{8,}|\b\d{10}\b")
NOISE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+\.\d+|\d{4}-\d{2}-\d{2}T[\d:.]+Z?")


# ---------------------------------------------------------------- job descriptions
def test_jd_create_list_get_update(client):
    r = client.post("/api/v1/job-descriptions", headers=A, json={"text": s.JD_BACKEND})
    assert r.status_code == 201
    jd = r.json()
    assert jd["title"] == "Senior Python Backend Developer"
    assert jd["spec"]["required_skills"] == ["python", "fastapi", "postgresql"]
    assert jd["spec"]["min_years"] == 3.0 and jd["spec"]["raw_text"].startswith("Senior Python")
    assert [x["id"] for x in client.get("/api/v1/job-descriptions", headers=A).json()["items"]] == [jd["id"]]
    spec = {**jd["spec"], "required_skills": ["Python", " python ", "Docker"], "preferred_skills": ["docker", "AWS"], "raw_text": ""}
    u = client.put(f"/api/v1/job-descriptions/{jd['id']}", headers=A, json={"title": "Edited", "spec": spec}).json()
    assert u["title"] == "Edited" and u["spec"]["required_skills"] == ["python", "docker"]
    assert u["spec"]["preferred_skills"] == ["aws"]  # de-duplicated against required
    assert u["spec"]["raw_text"].startswith("Senior Python")  # raw text kept
    assert client.get(f"/api/v1/job-descriptions/{jd['id']}", headers=A).json()["title"] == "Edited"


def test_jd_explicit_title_and_validation(client):
    r = client.post("/api/v1/job-descriptions", headers=A, json={"title": "My role", "text": s.JD_BACKEND})
    assert r.json()["title"] == "My role"
    bad = {"title": "x", "spec": {"required_skills": "python"}}
    jd = r.json()["id"]
    assert client.put(f"/api/v1/job-descriptions/{jd}", headers=A, json=bad).status_code == 422
    assert client.put(f"/api/v1/job-descriptions/{jd}", headers=A,
                      json={"spec": {"min_education": 9}}).status_code == 422


def test_jd_parser_failure_falls_back(client_factory, settings_factory):
    ml = MLAdapter.load("stub")
    ml.components["jd_parser"] = "real"  # pretend a real parser is installed and it crashes

    def broken(text, llm):
        raise RuntimeError("LLM down")

    ml._fns["jd_parser"] = broken
    c = client_factory(settings_factory(), ml=ml)
    r = c.post("/api/v1/job-descriptions", headers=A, json={"text": s.JD_BACKEND})
    assert r.status_code == 201 and r.json()["spec"]["required_skills"] == ["python", "fastapi", "postgresql"]


# ---------------------------------------------------------------- search
def test_search_ranks_the_matching_candidate_first(client):
    d = seed(client)
    t = time.perf_counter()
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"], "top_k": 5})
    elapsed = time.perf_counter() - t
    assert r.status_code == 200, r.text
    res = r.json()
    assert elapsed < 1.5, elapsed  # stub path is far below the 300 ms target; generous for CI
    top = res["results"][0]
    assert top["document_id"] == d["best_doc"]
    assert top["matched_required"] == ["python", "fastapi", "postgresql"] and top["missing_required"] == []
    assert [x["rank"] for x in res["results"]] == list(range(1, len(res["results"]) + 1))
    scores = [x["match_score"] for x in res["results"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= x <= 100 for x in scores)
    assert top["explanation"]["feature_contributions"] and top["retrieval"]["n_chunks_hit"] >= 1
    assert len(top["top_chunks"]) <= 3 and top["label"].startswith("Candidate ")
    assert set(res["timings"]) >= {"queries_ms", "embed_ms", "retrieve_ms", "rerank_ms", "total_ms"}
    assert res["embedder"] == "stub-hash-384" and res["warnings"] == []
    assert len(res["results"]) <= 5


def test_jd_without_recognised_skills_ranks_by_relevance_with_a_warning(client):
    """Non-tech JDs (chef, HR, ...) can have no skill the dictionary knows. The reranker would score everyone ~0
    (its coverage features are 0 for all), so search ranks by text relevance and tells the recruiter why."""
    seed(client)
    jd = client.post("/api/v1/job-descriptions", headers=A, json={
        "text": "Head Chef\nWe need an experienced, calm and friendly head chef to lead a small team in a busy restaurant."
    }).json()
    assert jd["spec"]["required_skills"] == []
    res = client.post("/api/v1/search", headers=A, json={"jd_id": jd["id"]}).json()
    assert res["results"] and res["reranker_version"] == "retrieval-order-no-required-skills"
    assert any(w.startswith("no_required_skills:") for w in res["warnings"])
    scores = [x["match_score"] for x in res["results"]]
    assert scores[0] == 100 and scores == sorted(scores, reverse=True)


def test_search_filters(client):
    d = seed(client)
    jd = d["jd"]["id"]
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd, "filters": {"required_skills": ["FastAPI", "postgresql"]}}).json()
    assert r["results"] and all({"fastapi", "postgresql"} <= set(x["top_skills"]) for x in r["results"])
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd, "filters": {"min_years": 6}}).json()
    assert all(x["years_experience"] >= 6 for x in r["results"])
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd, "filters": {"min_education": 4}}).json()
    assert all(x["education_level"] >= 4 for x in r["results"])
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd, "filters": {"required_skills": ["cobol"]}}).json()
    assert r["results"] == [] and r["warnings"] == ["no_matching_candidates"]
    assert client.post("/api/v1/search", headers=A, json={"jd_id": jd, "top_k": 0}).status_code == 422


def test_search_empty_library(client):
    jd = client.post("/api/v1/job-descriptions", headers=A, json={"text": s.JD_BACKEND}).json()
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd["id"]}).json()
    assert r["results"] == [] and r["warnings"] == ["no_ready_documents"]


def test_search_survives_hostile_ts_query_and_empty_query(client_factory, settings_factory):
    ml = MLAdapter.load("stub")
    from docint.contracts import Queries
    c = client_factory(settings_factory(), ml=ml)
    d = seed(c)
    for ts in ["python')); drop table chunks;--", "", "&&&|||", "the | and"]:
        ml._fns["queries"] = lambda job, ts=ts: Queries(dense_query="python fastapi", ts_query=ts)
        r = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]})
        assert r.status_code == 200, (ts, r.text)
        assert r.json()["results"], ts  # vector side still finds candidates


def test_reranker_failure_falls_back_to_retrieval_order(client_factory, settings_factory):
    ml = MLAdapter.load("stub")

    class Broken:
        version = "broken"

        def rank(self, job, cands):
            raise RuntimeError("model file missing")

    ml.reranker = Broken()
    c = client_factory(settings_factory(), ml=ml)
    d = seed(c)
    r = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    assert "reranker_failed" in r["warnings"] and r["reranker_version"] == "retrieval-order-fallback"
    assert r["results"] and r["results"][0]["match_score"] == 100.0


def test_reranker_partial_output_is_completed(client_factory, settings_factory):
    ml = MLAdapter.load("stub")
    inner = ml.reranker

    class Partial:
        version = "partial"

        def rank(self, job, cands):
            out = inner.rank(job, cands)[:2]
            return out + [out[0].model_copy(update={"candidate_id": "unknown-id"})]

    ml.reranker = Partial()
    c = client_factory(settings_factory(), ml=ml)
    d = seed(c)
    r = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"], "top_k": 20}).json()
    assert "reranker_incomplete" in r["warnings"]
    ids = [x["candidate_id"] for x in r["results"]]
    assert "unknown-id" not in ids and len(ids) == len(set(ids)) == 9


def test_stale_embeddings_warning(client, clean_db):
    d = seed(client)
    with db_conn(clean_db) as conn:
        conn.execute("update documents set embedder = 'old-model' where id = %s", (d["best_doc"],))
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    assert any(w.startswith("stale_embeddings: 1 ") for w in r["warnings"])


def test_search_reload_and_csv(client):
    d = seed(client)
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    assert r["jd_id"] == d["jd"]["id"]
    jds = client.get("/api/v1/job-descriptions", headers=A).json()["items"]
    assert jds[0]["last_search_id"] == r["search_id"] and jds[0]["required_skills"] == d["jd"]["spec"]["required_skills"]
    again = client.get(f"/api/v1/search/{r['search_id']}", headers=A).json()
    assert again["results"] == r["results"] and again["search_id"] == r["search_id"]
    assert client.get(f"/api/v1/search/{r['search_id']}", headers=B).status_code == 404
    c = client.get(f"/api/v1/search/{r['search_id']}/export.csv", headers=A)
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    assert "attachment" in c.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(c.text.lstrip("﻿"))))
    assert rows[0][0] == "rank" and len(rows) == len(r["results"]) + 1
    assert client.get(f"/api/v1/search/{r['search_id']}/export.csv", headers=B).status_code == 404


def test_blind_mode_never_leaks_pii(client):
    d = seed(client)
    best = d["best"]
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"], "top_k": 20})
    cand_id = r.json()["results"][0]["candidate_id"]
    cand = client.get(f"/api/v1/candidates/{cand_id}", headers=A)
    an = client.post("/api/v1/analysis", headers=A, json={"jd_id": d["jd"]["id"], "candidate_id": cand_id})
    for resp in (r, cand, an):
        text = resp.text
        assert best.name not in text and best.email not in text and best.phone not in text
        assert not EMAIL.search(text), EMAIL.search(text)
        assert not PHONE.search(NOISE.sub("", text)), PHONE.search(NOISE.sub("", text))
    assert all(x["display_name"] is None for x in r.json()["results"])
    assert cand.json()["pii"] is None


def test_reveal_is_audited(client):
    d = seed(client)
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    cid = r["results"][0]["candidate_id"]
    rv = client.get(f"/api/v1/candidates/{cid}?reveal=true", headers=A).json()
    assert rv["pii"]["full_name"] == d["best"].name and rv["pii"]["emails"] == [d["best"].email]
    assert rv["pii"]["email"] == d["best"].email and rv["pii"]["phone"]  # single values for the reveal panel
    assert rv["years_experience"] == rv["profile"]["years_experience"]
    audit = client.get("/api/v1/audit", headers=A).json()["items"]
    assert audit[0]["action"] == "pii_reveal" and audit[0]["target_id"] == cid
    nb = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"], "blind": False}).json()
    assert nb["results"][0]["display_name"] == d["best"].name and nb["blind"] is False
    assert client.get("/api/v1/audit", headers=A).json()["items"][0]["action"] == "search_reveal"


# ---------------------------------------------------------------- analysis
def test_analysis_citations_are_verbatim_and_cached(client):
    d = seed(client)
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    cid = r["results"][0]["candidate_id"]
    a = client.post("/api/v1/analysis", headers=A, json={"jd_id": d["jd"]["id"], "candidate_id": cid,
                                                         "search_id": r["search_id"]})
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["from_cache"] is False and body["citations_resolved"]
    for c in body["citations_resolved"]:
        assert c["text_scrubbed"][c["start"]:c["end"]] == c["quote"]  # 100 % verbatim
    statuses = {sk["skill"]: sk["status"] for sk in body["result"]["skills"]}
    assert statuses["python"] == "present" and statuses["aws"] == "missing"
    t = time.perf_counter()
    again = client.post("/api/v1/analysis", headers=A, json={"jd_id": d["jd"]["id"], "candidate_id": cid}).json()
    assert again["from_cache"] is True and again["result"] == body["result"] and time.perf_counter() - t < 0.2


def test_editing_the_jd_invalidates_the_cache(client):
    d = seed(client)
    jd = d["jd"]
    cid = client.post("/api/v1/search", headers=A, json={"jd_id": jd["id"]}).json()["results"][0]["candidate_id"]
    client.post("/api/v1/analysis", headers=A, json={"jd_id": jd["id"], "candidate_id": cid})
    spec = {**jd["spec"], "required_skills": ["python", "kubernetes"]}
    client.put(f"/api/v1/job-descriptions/{jd['id']}", headers=A, json={"spec": spec})
    a = client.post("/api/v1/analysis", headers=A, json={"jd_id": jd["id"], "candidate_id": cid}).json()
    assert a["from_cache"] is False
    assert "kubernetes" in [sk["skill"] for sk in a["result"]["skills"]]


def _tampered_ml(fake_quote: str | None = None, foreign_chunk: str | None = None):
    ml = MLAdapter.load("stub")
    real = ml._fns["skillgap"]

    def tampered(job, profile, chunks, llm):
        res = real(job, profile, chunks, llm)
        evil = []
        if fake_quote:
            evil.append(Citation(chunk_id=chunks[0].chunk_id, quote=fake_quote))
        if foreign_chunk:
            evil.append(Citation(chunk_id=foreign_chunk, quote="Python"))
        extra = SkillAssessment(skill="leadership", status="present", reasoning="LLM says so", citations=evil)
        return res.model_copy(update={"skills": [*res.skills, extra]})

    ml._fns["skillgap"] = tampered
    return ml


def test_fabricated_quote_is_dropped_by_the_backend(client_factory, settings_factory):
    c = client_factory(settings_factory(), ml=_tampered_ml(fake_quote="Led a team of 40 engineers at Google"))
    d = seed(c)
    cid = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()["results"][0]["candidate_id"]
    body = c.post("/api/v1/analysis", headers=A, json={"jd_id": d["jd"]["id"], "candidate_id": cid}).json()
    lead = next(sk for sk in body["result"]["skills"] if sk["skill"] == "leadership")
    assert lead["status"] == "missing" and lead["citations"] == []
    assert body["result"]["dropped_unverified_claims"] == 1
    assert all(x["skill"] != "leadership" for x in body["citations_resolved"])


def test_citation_to_another_candidates_chunk_is_dropped(client_factory, settings_factory, clean_db):
    ml = _tampered_ml()
    c = client_factory(settings_factory(), ml=ml)
    d = seed(c)
    res = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()["results"]
    with db_conn(clean_db) as conn:
        other_chunk = str(conn.execute("select id from chunks where candidate_id = %s limit 1",
                                       (res[1]["candidate_id"],)).fetchone()[0])
    ml._fns["skillgap"] = _tampered_ml(foreign_chunk=other_chunk)._fns["skillgap"]
    body = c.post("/api/v1/analysis", headers=A, json={"jd_id": d["jd"]["id"], "candidate_id": res[0]["candidate_id"]}).json()
    assert body["result"]["dropped_unverified_claims"] == 1
    assert all(x["chunk_id"] != other_chunk for x in body["citations_resolved"])


def test_analysis_errors(client_factory, settings_factory):
    ml = MLAdapter.load("stub")
    c = client_factory(settings_factory(ANALYSIS_TIMEOUT_S=0.2, LLM_RATE_LIMIT_PER_MIN=3), ml=ml)
    d = seed(c)
    cid = c.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()["results"][0]["candidate_id"]
    body = {"jd_id": d["jd"]["id"], "candidate_id": cid}

    def slow(*a):
        time.sleep(1)

    ml._fns["skillgap"] = slow
    r = c.post("/api/v1/analysis", headers=A, json=body)
    assert r.status_code == 504 and r.json()["error"]["code"] == "analysis_timeout"

    def boom(*a):
        raise RuntimeError("LLM 500")

    ml._fns["skillgap"] = boom
    r = c.post("/api/v1/analysis", headers=A, json=body)
    assert r.status_code == 502 and r.json()["error"]["code"] == "analysis_failed"
    # 3 per minute: JD create (1) + 2 analyses already used -> next is limited
    r = c.post("/api/v1/analysis", headers=A, json=body)
    assert r.status_code == 429 and "retry-after" in r.headers
    assert c.post("/api/v1/analysis", headers=A, json={**body, "candidate_id": "nope"}).status_code == 404


# ---------------------------------------------------------------- feedback
def test_feedback_upsert_and_validation(client):
    d = seed(client)
    r = client.post("/api/v1/search", headers=A, json={"jd_id": d["jd"]["id"]}).json()
    body = {"search_id": r["search_id"], "candidate_id": r["results"][0]["candidate_id"], "rating": 1, "note": "strong"}
    f1 = client.post("/api/v1/feedback", headers=A, json=body)
    assert f1.status_code == 201 and f1.json()["rating"] == 1
    f2 = client.post("/api/v1/feedback", headers=A, json={**body, "rating": -1, "note": None}).json()
    assert f2["id"] == f1.json()["id"] and f2["rating"] == -1
    assert client.post("/api/v1/feedback", headers=A, json={**body, "rating": 5}).status_code == 422
    assert client.post("/api/v1/feedback", headers=B, json=body).status_code == 404


# ---------------------------------------------------------------- tenant isolation
def test_user_b_cannot_touch_user_a(client):
    d = seed(client)
    jd = d["jd"]["id"]
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd}).json()
    cid, doc = r["results"][0]["candidate_id"], r["results"][0]["document_id"]
    checks = [
        client.get(f"/api/v1/job-descriptions/{jd}", headers=B),
        client.put(f"/api/v1/job-descriptions/{jd}", headers=B, json={"spec": d["jd"]["spec"]}),
        client.post("/api/v1/search", headers=B, json={"jd_id": jd}),
        client.get(f"/api/v1/search/{r['search_id']}", headers=B),
        client.post("/api/v1/analysis", headers=B, json={"jd_id": jd, "candidate_id": cid}),
        client.get(f"/api/v1/candidates/{cid}?reveal=true", headers=B),
        client.get(f"/api/v1/documents/{doc}/pdf-url", headers=B),
        client.delete(f"/api/v1/documents/{doc}", headers=B),
        client.get(f"/api/v1/jobs/{d['upload']['job_id']}", headers=B),
    ]
    for resp in checks:
        assert resp.status_code == 404, (resp.request.url, resp.status_code, resp.text)
    assert client.get("/api/v1/documents", headers=B).json()["total"] == 0
    assert client.get("/api/v1/job-descriptions", headers=B).json()["items"] == []
    assert client.get("/api/v1/audit", headers=B).json()["items"] == []
    # B's own search over the same text finds nothing of A's
    jd_b = client.post("/api/v1/job-descriptions", headers=B, json={"text": s.JD_BACKEND}).json()
    assert client.post("/api/v1/search", headers=B, json={"jd_id": jd_b["id"]}).json()["results"] == []


@pytest.mark.parametrize("path", ["/api/v1/candidates/not-a-uuid", "/api/v1/search/xyz", "/api/v1/job-descriptions/1"])
def test_bad_ids_are_404_not_500(client, path):
    assert client.get(path, headers=A).status_code == 404


def test_uploads_by_two_users_rank_independently(client):
    seed(client)
    upload(client, [("b.pdf", s.make_resume(999, role="backend", skills=["Python", "FastAPI", "PostgreSQL"], years=4).pdf)],
           headers=B)
    drain(client)
    jd_b = client.post("/api/v1/job-descriptions", headers=B, json={"text": s.JD_BACKEND}).json()
    res = client.post("/api/v1/search", headers=B, json={"jd_id": jd_b["id"], "top_k": 50}).json()["results"]
    assert len(res) == 1
    assert json.dumps(res).count("candidate_id") == 1


def test_skill_chips_count_implied_skills_like_the_reranker():
    """FastAPI implies Python in Track C's coverage feature; the chips must agree or the #1 candidate would show
    "python missing" (found with the real BGE model: a FastAPI-only backend engineer legitimately ranked first)."""
    from app.services.search import skills_with_implied

    have = skills_with_implied(["FastAPI", "PostgreSQL", "Redis"])
    assert {"python", "fastapi", "postgresql", "redis"} <= have
    assert "java" not in have and "django" not in have  # implication is one-way
