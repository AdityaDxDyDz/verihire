from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from ..auth import User, current_user
from ..config import Settings
from ..db import Database
from ..deps import get_db, get_ingestion, get_settings_dep, get_storage
from ..errors import not_found
from ..schemas.api import DocumentListResponse, JobResponse, PdfUrlResponse, RetryRequest, RetryResponse, UploadResponse
from ..services import documents as svc
from ..services.ingestion import IngestionService
from ..services.storage import LocalStorage, Storage

router = APIRouter(tags=["documents"])


@router.post("/documents/upload", response_model=UploadResponse, status_code=202,
             summary="Upload PDFs (multipart field 'files' or 'files[]'); starts async ingestion")
async def upload(request: Request,
                 files: list[UploadFile] | None = File(None, description="PDF files (<= 10 MB each, <= 100 files)"),
                 doc_type: str = Form("resume"),
                 user: User = Depends(current_user), db: Database = Depends(get_db),
                 storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings_dep),
                 ingestion: IngestionService = Depends(get_ingestion)):
    form = await request.form()
    all_files = [f for f in (files or []) if hasattr(f, "read")]
    all_files += [f for f in form.getlist("files[]") if hasattr(f, "read")]
    out = await svc.upload(db, storage, user.id, all_files, doc_type, settings.max_pdf_bytes,
                           settings.MAX_FILES_PER_REGISTER)
    if out["document_ids"]:
        ingestion.wake()
    return out


@router.post("/documents/retry", response_model=RetryResponse, summary="Re-run failed / needs_ocr / ready documents")
async def retry(body: RetryRequest, user: User = Depends(current_user), db: Database = Depends(get_db),
                ingestion: IngestionService = Depends(get_ingestion)):
    out = await svc.retry(db, user.id, body.document_ids)
    if out["job_id"]:
        ingestion.wake()
    return out


@router.get("/jobs/{job_id}", response_model=JobResponse, summary="Poll ingestion progress")
async def get_job(job_id: str, user: User = Depends(current_user), db: Database = Depends(get_db)):
    return await svc.get_job(db, user.id, job_id)


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(status: str | None = Query(None), q: str | None = Query(None, max_length=100),
                         page: int = Query(1, ge=1, le=10_000), page_size: int = Query(25, ge=1, le=100),
                         user: User = Depends(current_user), db: Database = Depends(get_db)):
    return await svc.list_documents(db, user.id, status, q, page, page_size)


@router.get("/documents/{document_id}/pdf-url", response_model=PdfUrlResponse, summary="Short-lived link to the original PDF (audited)")
async def pdf_url(document_id: str, user: User = Depends(current_user), db: Database = Depends(get_db),
                  storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings_dep)):
    return await svc.pdf_url(db, storage, user.id, document_id, settings.PDF_URL_TTL_S)


@router.delete("/documents/{document_id}", status_code=204, response_class=Response,
               summary="Delete document, candidate, chunks and stored file (audited)")
async def delete_document(document_id: str, user: User = Depends(current_user), db: Database = Depends(get_db),
                          storage: Storage = Depends(get_storage)):
    await svc.delete_document(db, storage, user.id, document_id)
    return Response(status_code=204)


@router.get("/files/local", include_in_schema=False)
async def local_file(key: str, exp: int, sig: str, storage: Storage = Depends(get_storage)):
    """Serves signed links in STORAGE_MODE=local (the signature is the authorisation)."""
    if not isinstance(storage, LocalStorage):
        raise not_found("File")
    path = storage.verify(key, exp, sig)
    return FileResponse(path, media_type="application/pdf", headers={
        "Content-Disposition": "inline", "Cache-Control": "private, max-age=60"})
