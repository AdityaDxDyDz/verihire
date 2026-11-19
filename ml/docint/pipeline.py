"""Offline job description batch processing and output writing."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from typing import Any

from docint.contracts import ExtractionError, JobSpec
from docint.ingest.extractor import extract_pdf
from docint.llm.jd_rules import rule_based_job_spec

SUPPORTED_TYPES = {".pdf", ".docx", ".txt"}


def _to_jobspec(parsed: Any) -> JobSpec:
    """Adapt parser output to the shared ranking contract.

    The real offline parser returns a JobSpec directly. Supporting dict input keeps
    the adapter convenient for compatible rule parser implementations.
    """
    if isinstance(parsed, JobSpec):
        return parsed
    if isinstance(parsed, dict):
        return JobSpec.model_validate(parsed)
    raise TypeError(f"expected JobSpec from JD parser, got {type(parsed).__name__}")


def _extract_text(path: Path) -> tuple[str, int, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        extracted = extract_pdf(path.read_bytes())
        text = "\n\n".join(page["text"] for page in extracted.pages)
        return clean_text(text), extracted.page_count, "pdf"
    if suffix == ".txt":
        return clean_text(path.read_text(encoding="utf-8-sig")), 1, "txt"
    if suffix == ".docx":
        text, file_type = extract_uploaded_text(path.name, path.read_bytes())
        return text, 1, file_type
    raise ExtractionError("unsupported", f"unsupported file type '{suffix}'")


def clean_text(text: str) -> str:
    """Apply consistent whitespace and Unicode cleanup to extracted document text."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace("\u200b", "")
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_uploaded_text(filename: str, content: bytes) -> tuple[str, str]:
    """Extract cleaned text from one uploaded PDF, DOCX, or TXT file."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            try:
                extracted = extract_pdf(content)
            except Exception as exc:
                code = getattr(exc, "code", "corrupt")
                message = getattr(exc, "message", "PDF could not be read")
                raise ExtractionError(str(code), str(message)) from exc
            text = "\n\n".join(page["text"] for page in extracted.pages)
        elif suffix == ".txt":
            text = content.decode("utf-8-sig")
        elif suffix == ".docx":
            with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
                xml = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join("".join(node.itertext()) for node in xml.findall(".//w:p", ns))
        else:
            raise ExtractionError("unsupported", f"unsupported file type '{suffix}'")
    except (UnicodeDecodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ExtractionError("corrupt", "file could not be read") from exc
    text = clean_text(text)
    if not text:
        raise ExtractionError("empty", "no extractable text in uploaded file")
    return text, suffix[1:]


def discover_files(input_path: Path) -> tuple[list[Path], Path]:
    """Return supported files in stable order and the root used for relative paths."""
    if input_path.is_file():
        return [input_path], input_path.parent
    files = sorted(
        (p for p in input_path.rglob("*") if p.is_file()
         and p.suffix.lower() in SUPPORTED_TYPES and not p.name.startswith(("~$", "."))),
        key=lambda p: p.as_posix().casefold(),
    )
    return files, input_path


def _document_id(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    slug = re.sub(r"[^a-z0-9]+", "_", Path(relative).with_suffix("").as_posix().casefold()).strip("_")
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:60]}_{digest}"


def process_file(path: Path, root: Path) -> tuple[JobSpec | None, dict[str, Any]]:
    started = time.perf_counter()
    relative = path.relative_to(root).as_posix()
    category = relative.split("/", 1)[0] if "/" in relative else None
    entry: dict[str, Any] = {"file": relative, "category": category, "status": "ok"}
    spec: JobSpec | None = None
    try:
        text, page_count, file_type = _extract_text(path)
        entry.update(file_type=file_type, pages=page_count, characters=len(text))
        if not text.strip():
            raise ExtractionError("empty", f"no extractable text in '{path.name}'")
        # The JD parser is fully offline and returns the shared JobSpec directly.
        spec = _to_jobspec(rule_based_job_spec(text)).model_copy(update={"raw_text": text})
        entry["fields_found"] = {
            "title": spec.title is not None,
            "required_skills": len(spec.required_skills),
            "preferred_skills": len(spec.preferred_skills),
            "min_years": spec.min_years is not None,
            "min_education": spec.min_education is not None,
            "responsibilities": len(spec.responsibilities),
        }
        entry["document_id"] = _document_id(path, root)
    except ExtractionError as exc:
        entry.update(status="failed", error_code=exc.code, error=exc.message)
    except Exception as exc:  # keep a malformed file from aborting a batch
        entry.update(status="failed", error_code=type(exc).__name__, error=str(exc)[:300])
    entry["seconds"] = round(time.perf_counter() - started, 3)
    return (spec if entry["status"] == "ok" else None), entry


def write_outputs(specs: list[JobSpec], report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [spec.model_dump(mode="json") for spec in specs]
    (out_dir / "jobspec_dataset.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "jobspec_dataset.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out_dir / "processing_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def process_batch(input_path: Path, out_dir: Path) -> tuple[int, dict[str, Any]]:
    files, root = discover_files(input_path)
    specs: list[JobSpec] = []
    entries: list[dict[str, Any]] = []
    for index, path in enumerate(files, 1):
        spec, entry = process_file(path, root)
        entries.append(entry)
        if spec is not None:
            specs.append(spec)
        status = "OK  " if entry["status"] == "ok" else "FAIL"
        detail = "" if entry["status"] == "ok" else f"  ({entry.get('error_code')})"
        print(f"[{index}/{len(files)}] {status} {entry['file']}{detail}")
    failed = sum(entry["status"] != "ok" for entry in entries)
    report = {"input": str(input_path), "total_files": len(files),
              "succeeded": len(specs), "failed": failed, "files": entries}
    write_outputs(specs, report, out_dir)
    print(f"\nProcessed {len(files)} file(s): {len(specs)} succeeded, {failed} failed.")
    print(f"Output written to: {out_dir.resolve()}")
    return (0 if failed == 0 else 1), report
