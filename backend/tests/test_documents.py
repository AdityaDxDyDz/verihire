"""Upload validation, library, jobs, delete, retry, PDF links."""

from __future__ import annotations

from conftest import A, B, db_conn, drain, upload
from devtools import synthpdf as s

PDF = s.make_resume(1).pdf


def test_upload_accepts_pdfs_and_reports_rejections(client, settings_factory):
    r = upload(client, [("a.pdf", s.make_resume(1).pdf), ("b.pdf", s.make_resume(2).pdf),
                        ("notes.txt", b"hello"), ("empty.pdf", b""), ("dup.pdf", s.make_resume(1).pdf)])
    assert r.status_code == 202
    body = r.json()
    assert len(body["document_ids"]) == 2 and body["job_id"]
    reasons = {x["filename"]: x["reason"] for x in body["rejected"]}
    assert reasons == {"notes.txt": "not_pdf", "empty.pdf": "empty_file", "dup.pdf": "duplicate"}


def test_upload_files_brackets_field_name(client):
    r = upload(client, [("a.pdf", PDF)], field="files[]")
    assert r.status_code == 202 and len(r.json()["document_ids"]) == 1


def test_upload_duplicate_of_library_file(client):
    upload(client, [("a.pdf", PDF)])
    r = upload(client, [("again.pdf", PDF)])
    assert r.json()["job_id"] is None and r.json()["rejected"][0]["reason"] == "duplicate"
    # ...but another user may upload the same file
    assert len(upload(client, [("a.pdf", PDF)], headers=B).json()["document_ids"]) == 1


def test_upload_limits(client_factory, settings_factory):
    c = client_factory(settings_factory(MAX_PDF_MB=0.001, MAX_FILES_PER_REGISTER=3))  # ~1 KB
    big = b"%PDF-1.4\n" + b"x" * 5000
    r = upload(c, [("big.pdf", big)])
    assert r.json()["rejected"][0]["reason"] == "too_large"
    r = upload(c, [(f"{i}.pdf", PDF) for i in range(4)])
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_many_files"


def test_upload_no_files_and_bad_doc_type(client):
    r = client.post("/api/v1/documents/upload", headers=A, data={"doc_type": "resume"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_files"
    r = client.post("/api/v1/documents/upload", headers=A, data={"doc_type": "contract"},
                    files=[("files", ("a.pdf", PDF, "application/pdf"))])
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_doc_type"


def test_upload_requires_auth(client):
    r = client.post("/api/v1/documents/upload", files=[("files", ("a.pdf", PDF, "application/pdf"))])
    assert r.status_code == 401


def test_job_progress_and_library(client):
    r = upload(client, [(f"r{i}.pdf", s.make_resume(10 + i).pdf) for i in range(3)] + [("scan.pdf", s.blank_pdf())])
    job_id = r.json()["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}", headers=A).json()
    assert job["status"] == "running" and job["total"] == 4 and job["done"] == 0  # queued counts as running
    drain(client)
    job = client.get(f"/api/v1/jobs/{job_id}", headers=A).json()
    assert job["status"] == "completed_with_errors" and job["done"] == 3 and job["failed"] == 1
    assert job["created_at"] and job["finished_at"]
    assert {d["status"] for d in job["documents"]} == {"ready", "needs_ocr"}

    lib = client.get("/api/v1/documents", headers=A).json()
    assert lib["total"] == 4 and lib["counts"]["ready"] == 3 and lib["counts"]["needs_ocr"] == 1 and lib["counts"]["all"] == 4
    ready = [d for d in lib["items"] if d["status"] == "ready"]
    assert all(d["candidate_id"] and d["page_count"] == 1 for d in ready)
    page = client.get("/api/v1/documents?page=2&page_size=3", headers=A).json()
    assert len(page["items"]) == 1 and page["page"] == 2
    assert client.get("/api/v1/documents?status=needs_ocr", headers=A).json()["total"] == 1
    assert client.get("/api/v1/documents?q=scan", headers=A).json()["total"] == 1
    assert client.get("/api/v1/documents?q=%25", headers=A).json()["total"] == 0  # LIKE wildcards escaped
    assert client.get("/api/v1/documents?status=bogus", headers=A).status_code == 422
    assert lib["counts"]["processing"] == 0
    assert all(d["skills"] and d["years_experience"] is not None and d["education_level"] is not None for d in ready)
    assert client.get("/api/v1/documents?status=processing", headers=A).json()["total"] == 0


def test_job_not_found_for_bad_or_foreign_ids(client):
    job_id = upload(client, [("a.pdf", PDF)]).json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=B).status_code == 404
    assert client.get("/api/v1/jobs/not-a-uuid", headers=A).status_code == 404


def test_pdf_url_serves_file_and_is_audited(client):
    doc = upload(client, [("a.pdf", PDF)]).json()["document_ids"][0]
    r = client.get(f"/api/v1/documents/{doc}/pdf-url", headers=A)
    assert r.status_code == 200 and r.json()["expires_in"] == 300
    f = client.get(r.json()["url"])
    assert f.status_code == 200 and f.content == PDF and f.headers["content-type"] == "application/pdf"
    assert client.get(r.json()["url"].replace("sig=", "sig=x")).status_code == 404
    audit = client.get("/api/v1/audit", headers=A).json()["items"]
    assert audit[0]["action"] == "pdf_url" and audit[0]["target_id"] == doc


def test_delete_removes_everything(client, clean_db):
    doc = upload(client, [("a.pdf", PDF)]).json()["document_ids"][0]
    drain(client)
    url = client.get(f"/api/v1/documents/{doc}/pdf-url", headers=A).json()["url"]
    assert client.delete(f"/api/v1/documents/{doc}", headers=B).status_code == 404
    r = client.delete(f"/api/v1/documents/{doc}", headers=A)
    assert r.status_code == 204 and r.content == b""
    with db_conn(clean_db) as conn:
        for t in ("documents", "candidates", "chunks", "candidate_pii"):
            assert conn.execute(f"select count(*) from {t}").fetchone()[0] == 0, t
    assert client.get(url).status_code == 404  # stored file gone
    assert client.delete(f"/api/v1/documents/{doc}", headers=A).status_code == 404
    assert client.get("/api/v1/audit", headers=A).json()["items"][0]["action"] == "document_delete"


def test_retry_only_failed_and_owned(client):
    r = upload(client, [("bad.pdf", s.corrupt_pdf()), ("ok.pdf", PDF)])
    bad, ok = r.json()["document_ids"]
    drain(client)
    assert client.post("/api/v1/documents/retry", headers=B, json={"document_ids": [bad]}).json()["job_id"] is None
    rr = client.post("/api/v1/documents/retry", headers=A, json={"document_ids": [bad, "junk"]}).json()
    assert rr["job_id"] and rr["document_ids"] == [bad]
    job = client.get(f"/api/v1/jobs/{rr['job_id']}", headers=A).json()
    assert job["status"] == "running" and job["total"] == 1
    drain(client)
    job = client.get(f"/api/v1/jobs/{rr['job_id']}", headers=A).json()
    assert job["documents"][0]["status"] == "failed" and job["failed"] == 1
    assert client.post("/api/v1/documents/retry", headers=A, json={"document_ids": []}).status_code == 422
