"""Load a REAL resume dataset (e.g. Kaggle) into a running VeriHire server and report how the system handles it.

Works with any of these layouts (auto-detected):
  * a folder of PDFs, optionally one sub-folder per category        e.g. hadikp/resume-data-pdf
        python scripts/import_kaggle.py --pdf-dir C:\\data\\resume-data-pdf --limit 200
  * a CSV with a resume-text column and/or a job-description column  e.g. pranavvenugo/resume-and-job-description
        python scripts/import_kaggle.py --csv C:\\data\\resume-jd\\data.csv --limit 200
    (resume text is rendered to simple PDFs so it goes through the real upload -> parse -> index pipeline)
  * both at once; add --jd-file my_jd.txt (repeatable) to search with your own job descriptions.
  * resumes from one dataset, job descriptions from ANOTHER CSV                e.g. snehaanbhawal/resume-dataset +
    adityarajsrv/job-descriptions-2025-tech-and-non-tech-roles
        python scripts/import_kaggle.py --pdf-dir C:\\data\\resume-dataset\\data\\data --limit 240 \\
               --jd-csv C:\\data\\jds\\<file>.csv --jd-limit 15 --jd-match "developer|engineer|accountant|teacher"
    When the resumes sit in category folders (ACCOUNTANT, HR, INFORMATION-TECHNOLOGY, ...) the report also gives a
    CATEGORY CHECK: for each JD whose title points to a category, the share of its top 10 from that category.

What you get: ingestion breakdown (ready / failed / needs_ocr + reasons), parsed-field coverage, and for each JD the
top-10 ranking with the source file / category / CSV row. If the CSV pairs a resume with a JD in the same row it also
reports where the paired resume ranks (hit@10, MRR) - a rough real-data sanity check, not a labelled benchmark.

Real resumes contain personal data: use a separate workspace (default token dev:kaggle@example.com), never commit the
data or kaggle_report.json, and respect the dataset licence.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import textwrap
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from devtools.synthpdf import make_pdf  # noqa: E402
from seed_demo import JDS  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


# ---------------------------------------------------------------- dataset readers
def text_to_pdf(text: str) -> bytes:
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        lines.extend(textwrap.wrap(para, 95) or [""])
    lines = [ln.encode("latin-1", "replace").decode("latin-1") for ln in lines if ln is not None]
    pages = [lines[i : i + 60] for i in range(0, max(len(lines), 1), 60)] or [[""]]
    return make_pdf(pages[:10])


def pick_column(headers: list[str], rows: list[dict], want: str) -> str | None:
    low = {h: h.lower().replace(" ", "_") for h in headers}
    if want == "resume":
        c = [h for h in headers if re.search(r"resume|\bcv\b|candidate", low[h]) and not re.search(r"id$|html|_id|url", low[h])]
        c.sort(
            key=lambda h: (("str" in low[h] or "text" in low[h]), avg_len(rows, h)),
            reverse=True,
        )
    elif want == "jd":
        c = [h for h in headers if re.search(r"job_?desc|^jd|jd_|description|posting", low[h]) and "resume" not in low[h]]
        c.sort(key=lambda h: avg_len(rows, h), reverse=True)
    else:  # category / title
        c = [h for h in headers if re.search(r"category|job_?title|position|role|label", low[h])]
        c.sort(key=lambda h: avg_len(rows, h))
    c = [h for h in c if want == "category" or avg_len(rows, h) >= (200 if want == "resume" else 40)]
    return c[0] if c else None


def avg_len(rows: list[dict], col: str) -> float:
    vals = [len(r.get(col) or "") for r in rows[:200]]
    return sum(vals) / max(len(vals), 1)


def read_table(path: Path) -> list[dict]:
    """Rows of a .csv, or of the first sheet of an .xlsx / .xls (Kaggle files are sometimes Excel)."""
    with path.open("rb") as f:
        head = f.read(8)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls") or head.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        try:
            import pandas as pd

            df = pd.read_excel(path, dtype=str).fillna("")
        except ImportError as e:
            sys.exit(f"{path.name} is an Excel file: pip install openpyxl xlrd   (or save it as CSV)  [{e}]")
        return [{str(k): str(v) for k, v in r.items()} for r in df.to_dict("records")]
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with path.open(encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def read_csv(path: Path, a) -> tuple[list[tuple[str, bytes, dict]], list[tuple[str, str, set[str]]]]:
    rows = read_table(path)
    if not rows:
        sys.exit(f"{path}: empty CSV")
    headers = list(rows[0].keys())
    rcol = a.resume_col or pick_column(headers, rows, "resume")
    jcol = a.jd_col or pick_column(headers, rows, "jd")
    ccol = a.category_col or pick_column(headers, rows, "category")
    print(f"CSV {path.name}: {len(rows)} rows, columns {headers}")
    print(f"  using resume column = {rcol!r}, JD column = {jcol!r}, category column = {ccol!r}")
    if not rcol and not jcol:
        sys.exit("could not find a resume or job-description text column - pass --resume-col / --jd-col")
    files, jd_to_files = [], defaultdict(set)
    for i, r in enumerate(rows):
        if rcol and len(files) < a.limit and len((r.get(rcol) or "").strip()) >= 100:
            cat = re.sub(r"[^A-Za-z0-9]+", "-", (r.get(ccol) or "")).strip("-")[:30] if ccol else ""
            name = f"csv-row{i:05d}{'-' + cat if cat else ''}.pdf"
            files.append(
                (
                    name,
                    text_to_pdf(r[rcol]),
                    {"source": f"row {i}", "category": r.get(ccol) if ccol else None},
                )
            )
            if jcol and (r.get(jcol) or "").strip():
                jd_to_files[r[jcol].strip()].add(name)
    jds = []
    if jcol:
        seen = Counter((r.get(jcol) or "").strip() for r in rows)
        ranked = sorted(
            (t for t in seen if len(t) >= 40),
            key=lambda t: (-len(jd_to_files.get(t, ())), -seen[t]),
        )
        for t in ranked[: a.jd_limit]:
            title = next((ln.strip() for ln in t.splitlines() if ln.strip()), "Job")[:80]
            jds.append((f"[csv] {title}", t[:20000], jd_to_files.get(t, set())))
    return files, jds


# resume-folder category (LiveCareer / snehaanbhawal names) -> regex on a JD title that points to it
CATEGORY_HINTS = {
    "ACCOUNTANT": r"accountant|accounting|bookkeep|audit",
    "ADVOCATE": r"advocate|lawyer|legal|attorney|paralegal",
    "AGRICULTURE": r"agricultur|farm|agronom",
    "APPAREL": r"apparel|fashion|garment|merchandis",
    "ARTS": r"\bart(s|ist)?\b|illustrat",
    "AUTOMOBILE": r"automobile|automotive|mechanic",
    "AVIATION": r"aviation|pilot|aircraft|airline",
    "BANKING": r"bank",
    "BPO": r"\bbpo\b|call cent|customer (service|support)",
    "BUSINESS-DEVELOPMENT": r"business develop",
    "CHEF": r"\bchef\b|\bcook\b|culinary|kitchen",
    "CONSTRUCTION": r"construction|site (engineer|supervisor)|foreman",
    "CONSULTANT": r"consultant",
    "DESIGNER": r"designer|\bux\b|\bui\b",
    "DIGITAL-MEDIA": r"digital media|social media|content (creator|writer)|video",
    "ENGINEERING": r"engineer",
    "FINANCE": r"financ|investment|treasury",
    "FITNESS": r"fitness|personal trainer|gym|coach",
    "HEALTHCARE": r"nurse|health|medical|clinic|doctor|physician|pharma",
    "HR": r"human resource|\bhr\b|recruit|talent",
    "INFORMATION-TECHNOLOGY": r"software|developer|programmer|devops|cloud|network|\bit\b|data|web|backend|"
    r"frontend|full.?stack|database|system admin|cyber|security analyst|\bqa\b",
    "PUBLIC-RELATIONS": r"public relations|\bpr\b|communications",
    "SALES": r"sales|account executive",
    "TEACHER": r"teacher|teaching|tutor|lecturer|educat",
}


def expected_categories(title: str, present: set[str]) -> set[str]:
    t = title.lower()
    return {c for c, rx in CATEGORY_HINTS.items() if c in present and re.search(rx, t)}


def _split_items(v: str) -> list[str]:
    v = v.strip().strip("[]")
    parts = re.split(r"\s*(?:;|\||\n|•|,(?=\s*[A-Za-z'\"]))\s*", v) if v else []
    return [p.strip(" '\"-") for p in parts if p.strip(" '\"-")]


def compose_jd(r: dict, cols: dict[str, str | None]) -> str:
    """JD text from a structured row (Title / ExperienceLevel / YearsOfExperience / Skills / Responsibilities /
    Keywords), laid out the way a recruiter writes one, so the normal JD parser reads it."""
    g = {k: (r.get(c) or "").strip() if c else "" for k, c in cols.items()}
    lines = [g["title"] or "Job"]
    if g["level"]:
        lines.append(f"Experience level: {g['level']}")
    lines.append("Requirements:")
    if g["years"]:
        y = g["years"] if re.search(r"year|yr", g["years"], re.I) else f"{g['years']} years"
        lines.append(f"- {y} of professional experience")
    skills = _split_items(g["skills"])
    if skills:
        lines.append(f"- Skills: {', '.join(skills)}")
    resp = _split_items(g["resp"])
    if resp:
        lines += ["Responsibilities:", *[f"- {x}" for x in resp]]
    if g["keywords"]:
        lines += ["Keywords:", f"- {', '.join(_split_items(g['keywords']))}"]
    return "\n".join(lines)


def read_jd_csv(path: Path, a) -> list[tuple[str, str, set[str]]]:
    """Job descriptions only (another dataset): pick --jd-limit of them, spread over titles / categories."""
    import random

    rows = read_table(path)
    if not rows:
        sys.exit(f"{path}: empty CSV")
    headers = list(rows[0].keys())
    low = {h: h.lower().replace(" ", "_") for h in headers}
    jcol = a.jd_col or pick_column(headers, rows, "jd")
    tcol = a.jd_title_col or next(
        (h for h in headers if re.search(r"title|role|position|designation", low[h]) and avg_len(rows, h) < 120), None
    )
    gcol = next((h for h in headers if re.search(r"category|domain|type|department|industry", low[h]) and h != jcol), None)
    print(f"JD CSV {path.name}: {len(rows)} rows, columns {headers}")

    def col(rx: str) -> str | None:
        return next((h for h in headers if re.search(rx, low[h]) and h not in (tcol, jcol)), None)

    level = col(r"level|seniority")
    parts = {
        "title": tcol,
        "level": level,
        "years": col(r"year") or next((h for h in headers if re.search(r"experience", low[h]) and h != level), None),
        "skills": col(r"skill|requirement|qualification"),
        "resp": col(r"respons|dut|task"),
        "keywords": col(r"keyword|tag"),
    }
    if not jcol and not (parts["skills"] or parts["resp"]):
        sys.exit("could not find the job-description text column - pass --jd-col")
    if not jcol:
        print(f"  no single text column: composing each JD from {[c for c in parts.values() if c]}")
    else:
        print(f"  using JD text column = {jcol!r}, title column = {tcol!r}, group column = {gcol!r}")
    items = []
    for r in rows:
        text = (r.get(jcol) or "").strip() if jcol else compose_jd(r, parts)
        title = (r.get(tcol) or "").strip() if tcol else ""
        if len(text) < 40:
            continue
        if a.jd_match and not re.search(a.jd_match, f"{title}\n{text}", re.I):
            continue
        if title and not text.lower().startswith(title.lower()):
            text = f"{title}\n{text}"  # the title line helps the JD parser and the reader
        items.append((title or text.splitlines()[0][:80], text[:20000], (r.get(gcol) or "") if gcol else ""))
    rng = random.Random(7)
    groups: dict[str, list] = defaultdict(list)
    for it in items:
        groups[(it[2] or it[0]).lower()].append(it)
    for g in groups.values():
        rng.shuffle(g)
    picked, keys = [], sorted(groups)
    rng.shuffle(keys)
    while len(picked) < a.jd_limit and any(groups[k] for k in keys):
        for k in keys:
            if groups[k] and len(picked) < a.jd_limit:
                picked.append(groups[k].pop())
    print(f"  {len(items)} JDs match, using {len(picked)}: {[t for t, _, _ in picked]}")
    return [(f"[jd] {t}"[:200], text, set()) for t, text, _ in picked]


def read_pdf_dir(root: Path, limit: int) -> list[tuple[str, bytes, dict]]:
    pdfs = sorted(p for p in root.rglob("*") if p.suffix.lower() == ".pdf")
    print(f"PDF folder {root}: {len(pdfs)} PDFs found, using {min(limit, len(pdfs))}")
    # take an even spread over categories instead of the first N of the first folder
    by_cat: dict[str, list[Path]] = defaultdict(list)
    for p in pdfs:
        by_cat[p.parent.name if p.parent != root else ""].append(p)
    picked: list[Path] = []
    while len(picked) < min(limit, len(pdfs)):
        for cat in sorted(by_cat):
            if by_cat[cat] and len(picked) < limit:
                picked.append(by_cat[cat].pop(0))
    out = []
    for p in picked:
        cat = p.parent.name if p.parent != root else ""
        out.append(
            (
                f"{cat + '__' if cat else ''}{p.name}"[-200:],
                p.read_bytes(),
                {"source": str(p), "category": cat or None},
            )
        )
    return out


# ---------------------------------------------------------------- server interaction
def upload(c: httpx.Client, files, batch: int, timeout: float) -> None:
    jobs, rejected = [], Counter()
    for i in range(0, len(files), batch):
        part = files[i : i + batch]
        r = c.post(
            "/documents/upload",
            files=[("files", (n, d, "application/pdf")) for n, d, _ in part],
        )
        if r.status_code != 202:
            sys.exit(f"upload failed: {r.status_code} {r.text[:300]}")
        b = r.json()
        rejected.update(x["reason"] for x in b["rejected"])
        if b["job_id"]:
            jobs.append(b["job_id"])
        print(f"  uploaded batch {i // batch + 1}: {len(b['document_ids'])} accepted, {len(b['rejected'])} rejected")
    if rejected:
        print(f"  rejected at upload: {dict(rejected)}  (duplicates = already in this workspace)")
    t0, pending = time.time(), list(jobs)
    while pending and time.time() - t0 < timeout:
        states = [c.get(f"/jobs/{j}").json() for j in pending]
        done, total = (
            sum(s["done"] + s["failed"] for s in states),
            sum(s["total"] for s in states),
        )
        print(
            f"\r  ingesting {done}/{total}  ({time.time() - t0:.0f}s)",
            end="",
            flush=True,
        )
        pending = [j for j, s in zip(pending, states, strict=True) if s["status"] == "running"]
        if pending:
            time.sleep(2)
    print()
    if pending:
        print("  ! timed out waiting - ingestion continues in the background; rerun later with --skip-upload")


def all_documents(c: httpx.Client) -> list[dict]:
    items, page = [], 1
    while True:
        r = c.get("/documents", params={"page": page, "page_size": 100, "status": "all"}).json()
        items += r["items"]
        if len(items) >= r["total"] or not r["items"]:
            return items
        page += 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf-dir", type=Path)
    p.add_argument("--csv", type=Path, help="CSV / Excel file with resume text (and optionally a JD column)")
    p.add_argument("--jd-csv", type=Path, help="CSV / Excel file with job descriptions only (another dataset)")
    p.add_argument("--jd-match", help='regex: only JDs whose title/text match, e.g. "developer|accountant"')
    p.add_argument("--jd-title-col")
    p.add_argument(
        "--jd-file",
        type=Path,
        action="append",
        default=[],
        help="text file with a job description",
    )
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument(
        "--token",
        default="dev:kaggle@example.com",
        help="dev:<email> or a Supabase access token",
    )
    p.add_argument("--limit", type=int, default=200, help="max resumes to upload from each source")
    p.add_argument("--jd-limit", type=int, default=5, help="max JDs taken from the CSV")
    (
        p.add_argument("--resume-col"),
        p.add_argument("--jd-col"),
        p.add_argument("--category-col"),
    )
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument(
        "--skip-upload",
        action="store_true",
        help="only run the searches / report on what is there",
    )
    p.add_argument("--out", type=Path, default=Path("kaggle_report.json"))
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles (cp1252) must not crash on odd file names
        stream.reconfigure(errors="replace")
    if not (a.pdf_dir or a.csv or a.jd_csv or a.skip_upload):
        p.error("give --pdf-dir and/or --csv (download the dataset from Kaggle and unzip it first)")

    files, jds = [], []
    if a.pdf_dir:
        files += read_pdf_dir(a.pdf_dir, a.limit)
    if a.csv:
        f2, j2 = read_csv(a.csv, a)
        files += f2
        jds += j2
    if a.jd_csv:
        jds += read_jd_csv(a.jd_csv, a)
    for jf in a.jd_file:
        t = jf.read_text(encoding="utf-8", errors="replace")
        jds.append((f"[file] {jf.stem}", t[:20000], set()))
    if not jds:  # PDF-only dataset: use the built-in demo JDs
        jds = [(f"[demo] {t}", txt, set()) for t, txt in JDS]
    meta = {n: m for n, _, m in files}

    c = httpx.Client(
        base_url=a.base_url.rstrip("/") + "/api/v1",
        headers={"Authorization": f"Bearer {a.token}"},
        timeout=180,
    )
    try:
        h = c.get("/health").json()
    except httpx.HTTPError as e:
        sys.exit(f"server not reachable at {a.base_url} ({e}). Start it first - see docs/TESTING_IRL.md, part B.")
    print(f"server {a.base_url}: {h['status']}, ML components {h['ml_components']}")
    if any(v == "stub" for k, v in h["ml_components"].items() if k in ("embedder", "reranker", "jd_parser")):
        print("  ! some ranking components are stubs - results will not reflect the real models (see README: ML_MODE)")
    if files and not a.skip_upload:
        t0 = time.time()
        upload(c, files, a.batch, a.timeout)
        print(f"  ingestion wall time {time.time() - t0:.0f}s for {len(files)} files")

    docs = all_documents(c)
    by_status = Counter(d["status"] for d in docs)
    reasons = Counter(d.get("error_code") or "?" for d in docs if d["status"] in ("failed", "needs_ocr"))
    ready = [d for d in docs if d["status"] == "ready"]
    cov = {k: sum(1 for d in ready if d.get(k) not in (None, [], "")) for k in ("years_experience", "education_level", "skills")}
    print(f"\nLIBRARY  {len(docs)} documents: {dict(by_status)}")
    if reasons:
        print(f"  not usable: {dict(reasons)}" + ("   <- image-only PDFs need OCR (not supported)" if by_status["needs_ocr"] else ""))
    if ready:
        print("  parsed fields on ready docs: " + ", ".join(f"{k} {v}/{len(ready)}" for k, v in cov.items()))
    hidden = [d["filename"] for d in docs if d.get("suspicious_hidden_text")]
    if hidden:
        print(f"  hidden-text warning on {len(hidden)} file(s), e.g. {hidden[:3]}")
    doc_file = {d["id"]: d["filename"] for d in docs}

    def category_of(fn: str) -> str | None:  # from this run's metadata, else from the "CATEGORY__file.pdf" name
        return (meta.get(fn) or {}).get("category") or (fn.split("__")[0] if "__" in fn else None)

    present = {c for c in (category_of(d["filename"]) for d in ready) if c}

    report = {
        "server": a.base_url,
        "ml_components": h["ml_components"],
        "library": dict(by_status),
        "not_usable_reasons": dict(reasons),
        "parsed_coverage": cov,
        "searches": [],
    }
    if not ready:
        print("\nno ready documents - nothing to search")
    hits, rr, n_paired = 0, 0.0, 0
    cat_prec: list[float] = []
    no_skills = 0
    for title, text, paired in jds if ready else []:
        r = c.post("/job-descriptions", json={"title": title[:200], "text": text})
        if r.status_code != 201:
            print(f"\nJD {title!r}: {r.status_code} {r.text[:200]}")
            continue
        jd = r.json()
        t0 = time.time()
        s = c.post("/search", json={"jd_id": jd["id"], "top_k": 100, "blind": True})
        ms = (time.time() - t0) * 1000
        if s.status_code != 200:
            print(f"\nsearch for {title!r}: {s.status_code} {s.text[:200]}")
            continue
        res = s.json()["results"]
        spec = jd["spec"]
        no_skills += not spec.get("required_skills")
        print(
            f"\nJD {title}\n  required {spec.get('required_skills')}, min years {spec.get('min_years')}, "
            f"edu {spec.get('min_education')}  -> {len(res)} ranked in {ms:.0f} ms; warnings {s.json()['warnings']}"
        )
        rows = []
        for i, x in enumerate(res[:10], 1):
            fn = doc_file.get(x["document_id"], "?")
            cat = category_of(fn)
            req = f"{len(x['matched_required'])}/{len(x['matched_required']) + len(x['missing_required'])} req"
            print(
                f"  {i:>2}. {x['match_score']:5.1f}  {req:<8} {x['years_experience']:4.1f}y  {fn[:56]:<56} "
                f"{('[' + cat + ']') if cat else ''}"
            )
            rows.append({"rank": i, "score": x["match_score"], "file": fn, "category": cat})
        entry = {"jd": title, "spec": spec, "latency_ms": round(ms), "top10": rows}
        if paired:
            ranks = [i for i, x in enumerate(res, 1) if doc_file.get(x["document_id"]) in paired]
            best = ranks[0] if ranks else None
            n_paired += 1
            hits += bool(best and best <= 10)
            rr += 1 / best if best else 0
            print(f"  paired resume(s) from the CSV rank: {ranks[:5] or 'not in top 100'}")
            entry["paired_ranks"] = ranks
        cats = Counter(r["category"] for r in rows if r["category"])
        if cats:
            print(f"  categories in top 10: {dict(cats.most_common())}")
        expect = expected_categories(re.sub(r"^\[\w+\]\s*", "", title), present)
        if expect and rows:
            share = sum(r["category"] in expect for r in rows) / len(rows)
            cat_prec.append(share)
            entry["expected_categories"], entry["category_precision@10"] = sorted(expect), share
            print(f"  category check: {share:.0%} of the top 10 are from {sorted(expect)}")
        report["searches"].append(entry)
    if n_paired:
        report["paired"] = {
            "jds": n_paired,
            "hit@10": hits / n_paired,
            "mrr": rr / n_paired,
        }
        print(
            f"\nPAIRED CHECK over {n_paired} JDs: hit@10 = {hits / n_paired:.2f}, MRR = {rr / n_paired:.2f}  "
            "(how often the resume the CSV pairs with a JD is ranked in the top 10)"
        )
    if cat_prec:
        report["category_check"] = {"jds": len(cat_prec), "mean_precision@10": sum(cat_prec) / len(cat_prec)}
        print(
            f"\nCATEGORY CHECK over {len(cat_prec)} JDs: on average {sum(cat_prec) / len(cat_prec):.0%} of the top 10 come "
            "from the resume category the JD title points to (rough: categories overlap, e.g. IT vs ENGINEERING)"
        )
    if no_skills:
        print(
            f"\nnote: {no_skills} JD(s) had no recognised required skills (the skills dictionary is mostly technical) - "
            "their ranking relies on text similarity only. Non-tech roles (chef, teacher, ...) are expected to be weaker."
        )
    a.out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nreport written to {a.out.resolve()}  (contains file names - do not commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
