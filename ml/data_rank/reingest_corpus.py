"""Make the ranking training data look like SERVING data (integration step, v1.2).

Ekant's corpus.jsonl holds "perfect" profiles written by the generator. In production, profiles come from
Harsh's parser (docint.ingest) reading a PDF. Training on perfect inputs and serving on parsed ones is a
train/serve skew. Measured example: the generator writes "(2 yrs)" but no dates, so the real parser returns
years_experience=None (-> 0) for EVERY resume, while the model was trained on the true years.

This script renders every corpus resume as a realistic PDF and runs it through the real pipeline:
  * experience lines get consecutive date ranges ("Mar 2022 - Oct 2026") derived from the truth years,
    ending at a FIXED month so the output is reproducible;
  * a fictional header (name, e-mail, phone) is added so PII scrubbing is exercised too;
  * docint.ingest.run_ingest -> real chunks + parsed profile;  embeddings from the chosen embedder.
Truth and labels are untouched (they come from the generator, never from parsing).

    python data_rank/reingest_corpus.py                 # stub embedder (as Ekant's corpus)
    python data_rank/reingest_corpus.py --embedder real # BGE-small (needs the cached model)
Writes data_rank/corpus_ingested.jsonl (same format as corpus.jsonl).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

END_YEAR, END_MONTH = 2026, 10  # fixed "present" for reproducibility
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_ROLE_RE = re.compile(r"\((\d+(?:\.\d+)?) yrs?\)")


def _month_index(y: int, m: int) -> int:
    return y * 12 + (m - 1)


def _fmt(idx: int) -> str:
    return f"{MONTHS[idx % 12]} {idx // 12}"


def add_dates(text: str, cursor: list[int]) -> str:
    """Replace each '(N yrs)' with a date range, newest role first (cursor walks back in time)."""
    def repl(m: re.Match) -> str:
        months = max(1, round(float(m.group(1)) * 12))
        end = cursor[0]
        start = end - months
        cursor[0] = start
        return f"({_fmt(start)} - {_fmt(end)})"
    return _ROLE_RE.sub(repl, text)


def render_pdf(doc: dict) -> bytes:
    import pymupdf

    t = doc["truth"]
    parts = (t.get("name") or "Alex Example").split()
    first, last = parts[0], (parts[-1] if len(parts) > 1 else "Example")
    header = f"{first} {last}\nEmail: {first.lower()}.{last.lower()}@example.com | Phone: +91 98765 43210\n"
    cursor = [_month_index(END_YEAR, END_MONTH)]
    body = "\n\n".join(add_dates(c["text_original"], cursor) for c in doc["chunks"])
    lines = (header + "\n" + body).split("\n")
    pdf = pymupdf.open()
    rect = pymupdf.Rect(50, 50, 560, 790)
    while lines:  # fill pages; never let text fall off a page (Harsh flags off-page text as hidden)
        page = pdf.new_page()
        take = len(lines)
        while take > 0:
            if page.insert_textbox(rect, "\n".join(lines[:take]), fontsize=9.5, fontname="helv") >= 0:
                break
            page.clean_contents()
            pdf.delete_page(-1)
            page = pdf.new_page()
            take -= 5
        lines = lines[max(take, 1):]
    data = pdf.tobytes()
    pdf.close()
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "corpus.jsonl"))
    ap.add_argument("--out", default=str(HERE / "corpus_ingested.jsonl"))
    ap.add_argument("--embedder", default="stub", choices=["stub", "real"])
    a = ap.parse_args()

    from docint.ingest.ingest import run_ingest

    if a.embedder == "real":
        from docint.embed import get_embedder
        emb = get_embedder()
    else:
        from data_rank.make_corpus import StubEmbedder
        emb = StubEmbedder()
    docs = [json.loads(line) for line in Path(a.corpus).read_text(encoding="utf-8").splitlines() if line.strip()]
    n_none = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for d in docs:
            ext, res = run_ingest(render_pdf(d), f"{d['doc_id']}.pdf")
            prof = {k: getattr(res.profile, k) for k in ("skills", "titles", "years_experience", "education_level",
                                                         "education_fields", "certifications", "summary", "parser_version")}
            if prof["years_experience"] is None:
                n_none += 1
                prof["years_experience"] = 0.0  # what the backend stores for "unknown"
            chunks = [{k: getattr(c, k) for k in ("chunk_index", "section", "page_no", "char_start", "char_end",
                                                  "text_original", "text_scrubbed", "token_count")} for c in res.chunks]
            vecs = emb.embed_documents([c.embedding_text() for c in res.chunks])
            f.write(json.dumps({"doc_id": d["doc_id"], "profile": prof, "chunks": chunks,
                                "embedding": [list(map(float, v)) for v in vecs], "truth": d["truth"],
                                "suspicious_hidden_text": ext.suspicious_hidden_text}) + "\n")
    print(f"re-ingested {len(docs)} resumes through docint.ingest ({emb.name}); years unknown: {n_none}; -> {a.out}")


if __name__ == "__main__":
    main()
