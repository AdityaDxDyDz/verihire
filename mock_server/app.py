"""Fixture-backed mock of the VeriHire API plus the static frontend.

Run:  uvicorn mock_server.app:app --port 8000      then open http://localhost:8000/
Purpose: lets the frontend be exercised against REAL HTTP (auth header, multipart upload, error envelope) before the
real backend exists. It is intentionally simple: reads are served from docs/contracts/fixtures, writes are accepted and
answered with fixture-shaped responses. For a smarter in-browser demo use `?mock=1` instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "docs" / "contracts" / "fixtures"
FRONTEND = ROOT / "frontend"
MAX_BYTES = 10 * 1024 * 1024


def fx(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


app = FastAPI(title="VeriHire mock API", version="0.1")


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/v1") and p != "/api/v1/health":
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            return err(401, "unauthorized", "Missing or invalid credentials")
    return await call_next(request)


@app.get("/api/v1/health")
def health():
    return fx("health")


@app.get("/api/v1/me")
def me():
    return fx("me")


@app.post("/api/v1/documents/upload")
async def upload(files: list[UploadFile] = File(...), doc_type: str = Form("resume")):
    ok, rejected = [], []
    for f in files:
        data = await f.read()
        if not (f.filename or "").lower().endswith(".pdf"):
            rejected.append({"filename": f.filename, "reason": "Only PDF files are accepted."})
        elif len(data) > MAX_BYTES:
            rejected.append({"filename": f.filename, "reason": "File is larger than 10 MB."})
        else:
            ok.append(f.filename)
    if not ok:
        return err(400, "no_valid_files", "None of the files could be accepted.")
    base = fx("upload_response")
    base["document_ids"] = base["document_ids"][: len(ok)] or base["document_ids"][:1]
    base["rejected"] = rejected
    return base


@app.post("/api/v1/documents/retry")
def retry():
    return fx("retry_response")


@app.get("/api/v1/jobs/{job_id}")
def job(job_id: str):
    return fx("job_completed_with_errors")


@app.get("/api/v1/documents")
def documents(status: str | None = None, q: str | None = None, page: int = 1, page_size: int = 20):
    d = fx("documents")
    items = d["items"]
    if status == "processing":
        items = [i for i in items if i["status"] not in ("ready", "failed", "needs_ocr")]
    elif status:
        items = [i for i in items if i["status"] == status]
    if q:
        items = [i for i in items if q.lower() in i["filename"].lower()]
    start = (page - 1) * page_size
    return {**d, "items": items[start : start + page_size], "total": len(items), "page": page, "page_size": page_size}


@app.get("/api/v1/documents/{doc_id}/pdf-url")
def pdf_url(doc_id: str):
    return {"url": "/fixtures/sample_resume.pdf", "expires_in": 300}


@app.delete("/api/v1/documents/{doc_id}", status_code=204)
def delete_doc(doc_id: str):
    return PlainTextResponse("", status_code=204)


@app.post("/api/v1/job-descriptions")
async def create_jd(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if len(text) < 40:
        return err(422, "validation_error", "text: String should have at least 40 characters")
    jd = fx("jd")
    jd["spec"]["raw_text"] = text
    return jd


@app.get("/api/v1/job-descriptions")
def list_jds():
    return fx("jd_list")


@app.get("/api/v1/job-descriptions/{jd_id}")
def get_jd(jd_id: str):
    return fx("jd")


@app.put("/api/v1/job-descriptions/{jd_id}")
async def put_jd(jd_id: str, request: Request):
    body = await request.json()
    jd = fx("jd")
    jd["spec"] = body["spec"]
    jd["title"] = body.get("title") or jd["title"]
    return jd


@app.post("/api/v1/search")
def search():
    return fx("search")


@app.get("/api/v1/search/{search_id}")
def get_search(search_id: str):
    return fx("search")


@app.get("/api/v1/search/{search_id}/export.csv")
def export_csv(search_id: str):
    rows = ["rank,label,match_score"] + [f'{r["rank"]},{r["label"]},{r["match_score"]}' for r in fx("search")["results"]]
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.post("/api/v1/analysis")
async def analysis(request: Request):
    body = await request.json()
    a = fx("analyses").get(body.get("candidate_id"))
    return a if a else err(404, "not_found", "Candidate not found")


@app.get("/api/v1/candidates/{cid}")
def candidate(cid: str, reveal: bool = False):
    c = fx("candidates").get(cid)
    if not c:
        return err(404, "not_found", "Candidate not found")
    if reveal:
        c["pii"] = fx("pii").get(cid)
    return c


@app.post("/api/v1/feedback", status_code=204)
def feedback():
    return PlainTextResponse("", status_code=204)


app.mount("/fixtures", StaticFiles(directory=FRONTEND / "fixtures"), name="fixtures")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
