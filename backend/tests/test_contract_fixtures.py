"""Contract tests between frontend (Kadam) and backend (Raj).

1. Every JSON fixture in docs/contracts/fixtures/ validates against backend/app/schemas/api.py.
   File names are matched loosely (``search.json``, ``post_search.json``, ``search_response.json``...).
2. Every JSON API route declares a response_model (so FastAPI validates what we send).
3. With WRITE_EXAMPLES=1 the full flow is run and every real response is written to
   docs/contracts/examples_backend/ (+ openapi.backend.json) - hand these to the frontend.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.schemas import api as M
from conftest import ROOT, A, seed

FIXTURES = ROOT / "docs" / "contracts" / "fixtures"            # Kadam's consumer-driven fixtures (mock mode)
FRONT_FIXTURES = ROOT / "frontend" / "fixtures"                  # the copy the browser loads
EXAMPLES = ROOT / "docs" / "contracts" / "examples_backend"      # real responses written by this backend
MODELS = {
    "health": M.HealthResponse, "me": M.MeResponse, "upload": M.UploadResponse, "documents_upload": M.UploadResponse,
    "retry": M.RetryResponse, "documents_retry": M.RetryResponse, "job": M.JobResponse, "jobs": M.JobResponse,
    "documents": M.DocumentListResponse, "documents_list": M.DocumentListResponse, "library": M.DocumentListResponse,
    "pdf_url": M.PdfUrlResponse, "documents_pdf_url": M.PdfUrlResponse,
    "job_description": M.JobDescriptionResponse, "job_descriptions_create": M.JobDescriptionResponse,
    "jd": M.JobDescriptionResponse, "job_descriptions": M.JobDescriptionListResponse,
    "job_descriptions_list": M.JobDescriptionListResponse, "search": M.SearchResponse,
    "analysis": M.AnalysisResponse, "candidate": M.CandidateResponse,
    "candidate_reveal": M.CandidateResponse, "feedback": M.FeedbackResponse, "error": M.ErrorResponse,
    "audit": M.AuditListResponse,
}
# Kadam's fixture files (frontend mock data): single responses, {id: response} maps, lists, {status: error}
KADAM_SINGLE = {"health": M.HealthResponse, "me": M.MeResponse, "upload_response": M.UploadResponse,
                "retry_response": M.RetryResponse, "job_running": M.JobResponse, "job_completed_with_errors": M.JobResponse,
                "documents": M.DocumentListResponse, "jd": M.JobDescriptionResponse, "jd_list": M.JobDescriptionListResponse,
                "search": M.SearchResponse, "pdf_url": M.PdfUrlResponse}
KADAM_MAPPED = {"candidates": M.CandidateResponse, "analyses": M.AnalysisResponse, "pii": M.PiiOut}
KADAM_LISTS = {"pool": M.Result}
KADAM_MOCK_ONLY = {"chunks"}  # internal data of the in-browser mock, not an API response


def _key(path: Path) -> str:
    k = path.stem.lower().replace("-", "_").replace(".", "_")
    k = re.sub(r"^(get|post|put|delete)_", "", k)
    k = re.sub(r"_(response|example|fixture|\d+)$", "", k)
    k = re.sub(r"^api_v1_", "", k)
    return k


def _check(model, data, where: str) -> None:
    try:
        model.model_validate(data)
    except ValidationError as e:
        pytest.fail(f"{where} does not match {model.__name__} (backend/app/schemas/api.py):\n{e}")


def _kadam_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json")) if FIXTURES.is_dir() else []


@pytest.mark.parametrize("path", _kadam_files() or [None], ids=lambda p: p.name if p else "no-fixtures")
def test_frontend_fixture_matches_backend_models(path):
    """The UI was built on these fixtures, so they must have exactly the shape the real backend sends."""
    if path is None:
        pytest.skip("no frontend fixtures yet")
    name = path.stem
    data = json.loads(path.read_text(encoding="utf-8"))
    if name in KADAM_SINGLE:
        _check(KADAM_SINGLE[name], data, path.name)
    elif name in KADAM_MAPPED:
        for key, value in data.items():
            _check(KADAM_MAPPED[name], value, f"{path.name}[{key}]")
    elif name in KADAM_LISTS:
        for i, value in enumerate(data):
            _check(KADAM_LISTS[name], value, f"{path.name}[{i}]")
    elif name == "errors":
        for status, value in data.items():
            _check(M.ErrorResponse, value, f"{path.name}[{status}]")
    elif name in KADAM_MOCK_ONLY:
        pytest.skip(f"{path.name} is internal mock data")
    else:
        pytest.fail(f"{path.name}: unknown fixture - add it to the mapping in this test")


def test_frontend_fixture_copies_are_identical():
    if not FRONT_FIXTURES.is_dir():
        pytest.skip("no frontend yet")
    for p in _kadam_files():
        twin = FRONT_FIXTURES / p.name
        assert twin.exists() and twin.read_bytes() == p.read_bytes(), f"frontend/fixtures/{p.name} differs from docs copy"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")) if EXAMPLES.is_dir() else [None],
                         ids=lambda p: p.name if p else "no-examples")
def test_backend_examples_match_models(path):
    if path is None or path.name.startswith("openapi"):
        pytest.skip("no examples")
    model = MODELS.get(_key(path))
    if model is None:
        pytest.skip(f"unmapped example {path.name}")
    _check(model, json.loads(path.read_text(encoding="utf-8")), path.name)


def test_every_json_route_declares_a_response_model(client):
    exempt = {"/api/v1/files/local", "/api/v1/{path:path}", "/api/v1/search/{search_id}/export.csv",
              "/api/v1/documents/{document_id}"}  # file / catch-all / csv / 204
    for route in client.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1") and route.path not in exempt:
            assert route.response_model is not None, route.path


@pytest.mark.skipif(os.environ.get("WRITE_EXAMPLES") != "1", reason="set WRITE_EXAMPLES=1 to regenerate examples")
def test_write_backend_examples(client):
    out = ROOT / "docs" / "contracts" / "examples_backend"
    out.mkdir(parents=True, exist_ok=True)
    d = seed(client, n_random=4)
    jd = d["jd"]
    r = client.post("/api/v1/search", headers=A, json={"jd_id": jd["id"], "top_k": 3}).json()
    cid = r["results"][0]["candidate_id"]
    examples = {
        "health": client.get("/api/v1/health").json(),
        "me": client.get("/api/v1/me", headers=A).json(),
        "documents_upload": d["upload"],
        "job": client.get(f"/api/v1/jobs/{d['upload']['job_id']}", headers=A).json(),
        "documents_list": client.get("/api/v1/documents?page_size=3", headers=A).json(),
        "pdf_url": client.get(f"/api/v1/documents/{d['best_doc']}/pdf-url", headers=A).json(),
        "job_description": jd,
        "job_descriptions_list": client.get("/api/v1/job-descriptions", headers=A).json(),
        "search": r,
        "analysis": client.post("/api/v1/analysis", headers=A, json={"jd_id": jd["id"], "candidate_id": cid}).json(),
        "candidate": client.get(f"/api/v1/candidates/{cid}", headers=A).json(),
        "feedback": client.post("/api/v1/feedback", headers=A,
                                json={"search_id": r["search_id"], "candidate_id": cid, "rating": 1}).json(),
        "error": client.get("/api/v1/candidates/nope", headers=A).json(),
    }
    for name, body in examples.items():
        MODELS[name].model_validate(body)
        (out / f"{name}.json").write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "openapi.backend.json").write_text(json.dumps(client.get("/openapi.json").json(), indent=2) + "\n")
