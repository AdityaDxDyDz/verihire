"""Show EXACTLY what VeriHire extracts from a resume - and what it probably missed. No server or database needed.

    python scripts/inspect_resume.py my_resume.pdf
    python scripts/inspect_resume.py my_resume.pdf --jd my_jd.txt      # + how it matches a job description
    python scripts/inspect_resume.py data\\resumes --limit 200           # a folder: summary + most-missed skills
    python scripts/inspect_resume.py my_resume.pdf --show-pii --show-text

Runs the real ingestion pipeline (Track B: extraction, PII scrubbing, sections/chunks, profile) and the real
skill matcher (Track C). Use it to check your own resume, and on a folder of real resumes to find what to add to
ml/docint/ingest/skills_dict.json (skills) or the chunker's section headings - the main way to make parsing robust.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml"))

from docint.ingest import chunker as _chunker  # noqa: E402
from docint.ingest.extractor import ExtractionError  # noqa: E402
from docint.ingest.ingest import run_ingest  # noqa: E402
from docint.rank.skillmatch import canonicalize, find_skills  # noqa: E402

EDU = {0: "none found", 1: "diploma", 2: "bachelor", 3: "master", 4: "PhD"}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"({_MONTH}\s*(\d{{4}}|['\u2019]\d{{2}})|\d{{1,2}}/\d{{4}}|\b(19|20)\d{{2}})"
DATE_RANGE = re.compile(rf"{_DATE}\s*(-|\u2013|\u2014|\u00b7|to|till|until)\s*({_DATE}|present|current|now|till date|"
                        rf"today|ongoing)", re.I)
EDU_WORDS = re.compile(r"\b(b\.?\s?tech|b\.?e\b|b\.?sc|bachelor|m\.?\s?tech|m\.?sc|master|mba|ph\.?d|diploma|"
                       r"degree|university|college|institute)\b", re.I)
STOP = {"and", "or", "with", "using", "in", "of", "the", "a", "an", "to", "for", "on", "etc", "including", "skills",
        "tools", "languages", "frameworks", "technologies", "others", "other", "basic", "advanced", "intermediate",
        "proficient", "familiar", "knowledge", "experience", "good", "strong", "excellent"}


def mask(s: str) -> str:
    if "@" in s:
        a, _, d = s.partition("@")
        return f"{a[:1]}***@{d}"
    return s[:3] + "*" * max(0, len(s) - 5) + s[-2:] if len(s) > 5 else "***"


def skill_items(text: str) -> list[str]:
    """Items as listed in a skills section: split on commas, bullets, pipes, slashes, 'Label:' prefixes."""
    out = []
    for line in text.splitlines():
        if _chunker._heading_section(line.strip().rstrip(":")):
            continue  # the section heading itself
        line = re.sub(r"^[\s\-*•·●▪◦]+", "", line)
        line = line.split(":", 1)[1] if re.match(r"^[A-Za-z /&]{2,30}:", line) else line
        for part in re.split(r"[,;|•·●▪]|\s/\s|\(|\)", line):
            p = part.strip(" .-\t")
            if 1 < len(p) <= 40 and len(p.split()) <= 4 and not p.isdigit():
                out.append(p)
    return out


def missed_skills(chunks) -> list[str]:
    """Items in the Skills section(s) that the dictionary does not recognise -> candidates to add."""
    missed: list[str] = []
    for c in chunks:
        if c.section != "skills":
            continue
        for item in skill_items(c.text_original):
            if item.lower() in STOP or canonicalize(item) or find_skills(item):
                continue
            if any(ch.isalpha() for ch in item) and item not in missed:
                missed.append(item)
    return missed


def unknown_headings(pages, name: str | None = None) -> list[str]:
    """Short, heading-like lines that are NOT in the section vocabulary - content under them may be mis-filed."""
    found = []
    skip = {(name or "").strip().lower()}
    for p in pages:
        for raw in p["text"].splitlines():
            t = raw.strip().rstrip(":").strip()
            if not (3 <= len(t) <= 40) or any(ch.isdigit() for ch in t) or t.endswith((".", ",")):
                continue
            words = t.split()
            if len(words) > 4 or "@" in t or "|" in t or "," in t:
                continue
            is_caps = t.isupper() or all(w[:1].isupper() for w in words if w[:1].isalpha())
            if is_caps and _chunker._heading_section(t) is None and t.lower() not in found and t.lower() not in skip:
                if t.isupper() or raw.strip().endswith(":") or len(words) <= 3:
                    found.append(t)
    return found


def inspect(path: Path, args) -> dict:
    data = path.read_bytes()
    try:
        doc, res = run_ingest(data, path.name)
    except ExtractionError as e:
        return {"file": path.name, "ok": False, "error": f"{e.code}: {e}"}
    full = "\n".join(p["text"] for p in doc.pages)
    prof = res.profile
    years = float(prof.years_experience or 0)
    edu = int(prof.education_level or 0)
    sections = Counter(c.section for c in res.chunks)
    checks: list[tuple[str, str]] = []

    def check(ok: bool, good: str, bad: str):
        checks.append(("OK" if ok else "!!", good if ok else bad))

    check(not doc.is_scanned, "text layer found", "no text layer (scanned/image PDF) -> needs OCR, nothing can be read")
    check(not doc.suspicious_hidden_text, "no hidden text", "hidden/white text found (flagged to the recruiter)")
    check(bool(res.pii.emails), "e-mail found", "no e-mail found (interview e-mails will skip this candidate)")
    check(bool(res.pii.full_name), "name found", "name not found (greeting will be generic; name may stay unredacted)")
    check("experience" in sections, "experience section found",
          "no experience section recognised -> years/titles may be missing (see unknown headings)")
    check("skills" in sections, "skills section found", "no skills section recognised (skills only found in text)")
    n_ranges = len(DATE_RANGE.findall(full))
    check(not (n_ranges and years == 0), f"years of experience: {years:g}",
          f"{n_ranges} date range(s) in the text but 0 years computed -> date format not understood")
    check(not (EDU_WORDS.search(full) and edu == 0), f"education: {EDU[edu]}",
          "education words present but no level detected")
    miss = missed_skills(res.chunks)
    unk = unknown_headings(doc.pages, res.pii.full_name)
    check(not miss, "every listed skill recognised", f"{len(miss)} listed skill(s) not in the dictionary: {miss[:8]}")
    out = {
        "file": path.name, "ok": True, "pages": doc.page_count, "scanned": doc.is_scanned,
        "hidden_text": doc.suspicious_hidden_text,
        "pii": {"name": res.pii.full_name, "emails": res.pii.emails, "phones": res.pii.phones, "urls": res.pii.urls},
        "sections": dict(sections), "unknown_headings": unk,
        "profile": {"years_experience": years, "education": EDU[edu],
                    "education_fields": list(prof.education_fields or []), "titles": list(prof.titles or []),
                    "certifications": list(prof.certifications or []), "skills": list(prof.skills or [])},
        "missed_skills": miss, "checks": checks,
    }
    if args.jd:
        from docint.llm.jd_parser import _normalize
        from docint.llm.jd_rules import rule_based_job_spec
        from docint.rank.skillmatch import expand_implied

        jd_text = Path(args.jd).read_text(encoding="utf-8", errors="replace")
        spec = _normalize(rule_based_job_spec(jd_text), jd_text)
        have = expand_implied(list(prof.skills or []))
        out["jd"] = {"required": spec.required_skills, "preferred": spec.preferred_skills, "min_years": spec.min_years,
                     "min_education": spec.min_education,
                     "matched_required": [s for s in spec.required_skills if s in have],
                     "missing_required": [s for s in spec.required_skills if s not in have],
                     "matched_preferred": [s for s in spec.preferred_skills if s in have]}
    if args.show_text:
        out["chunks"] = [{"section": c.section, "page": c.page_no, "text": c.text_scrubbed} for c in res.chunks]
    return out


def print_one(r: dict, show_pii: bool) -> None:
    print(f"\n=== {r['file']} ===")
    if not r["ok"]:
        print(f"  !! cannot read: {r['error']}")
        return
    pii = r["pii"]
    shown = (lambda x: x) if show_pii else mask
    print(f"  pages {r['pages']}, sections {r['sections']}")
    print(f"  personal details (removed before indexing): name {shown(pii['name']) if pii['name'] else '-'}; "
          f"e-mails {[shown(e) for e in pii['emails']]}; phones {[shown(p) for p in pii['phones']]}; "
          f"links {len(pii['urls'])}")
    p = r["profile"]
    print(f"  years {p['years_experience']:g} | education {p['education']} {p['education_fields'] or ''} | "
          f"titles {p['titles'][:4]}")
    if p["certifications"]:
        print(f"  certifications {p['certifications'][:5]}")
    print(f"  skills ({len(p['skills'])}): {', '.join(p['skills'])}")
    for flag, msg in r["checks"]:
        print(f"  [{flag}] {msg}")
    if r["unknown_headings"]:
        print(f"  [??] heading-like lines not recognised as sections: {r['unknown_headings'][:8]}")
    if "jd" in r:
        j = r["jd"]
        print(f"  JD: required {j['required']} | min years {j['min_years']} | min education {j['min_education']}")
        print(f"      matched {j['matched_required']}  missing {j['missing_required']}  "
              f"preferred matched {j['matched_preferred']}")
    for c in r.get("chunks", []):
        print(f"  --- [{c['section']} p{c['page']}] {c['text'][:400]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="a PDF or a folder of PDFs")
    ap.add_argument("--jd", help="text file with a job description to match against")
    ap.add_argument("--limit", type=int, default=200, help="max PDFs from a folder")
    ap.add_argument("--show-pii", action="store_true", help="print personal details unmasked (your own resume)")
    ap.add_argument("--show-text", action="store_true", help="print every chunk (scrubbed) with its section")
    ap.add_argument("--json", type=Path, help="also write the full result as JSON")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    if a.path.is_dir():
        files = sorted(p for p in a.path.rglob("*") if p.suffix.lower() == ".pdf")[: a.limit]
    else:
        files = [a.path]
    if not files:
        print("no PDFs found")
        return 1
    results = [inspect(f, a) for f in files]
    if len(results) == 1:
        print_one(results[0], a.show_pii)
    else:
        ok = [r for r in results if r["ok"]]
        print(f"{len(results)} PDFs: {len(ok)} readable, {sum(r['scanned'] for r in ok)} scanned, "
              f"{len(results) - len(ok)} unreadable")
        problems = Counter(re.sub(r"\d+", "N", m.split(" ->")[0].split(":")[0]) for r in ok for f, m in r["checks"]
                           if f == "!!")
        print("most common problems:")
        for m, n in problems.most_common(10):
            print(f"  {n:>4} x {m}")
        avg = lambda k: sum(len(r["profile"][k]) for r in ok) / max(len(ok), 1)  # noqa: E731
        print(f"average skills found per resume: {avg('skills'):.1f}; with years > 0: "
              f"{sum(r['profile']['years_experience'] > 0 for r in ok)}/{len(ok)}; with education: "
              f"{sum(r['profile']['education'] != 'none found' for r in ok)}/{len(ok)}")
        miss = Counter(s.lower() for r in ok for s in r["missed_skills"])
        print("\nskills most often NOT recognised (candidates for ml/docint/ingest/skills_dict.json):")
        for s, n in miss.most_common(40):
            print(f"  {n:>4} x {s}")
        heads = Counter(h.lower() for r in ok for h in r["unknown_headings"])
        print("\nheading-like lines most often not recognised (candidates for the chunker's section list):")
        for h, n in heads.most_common(20):
            print(f"  {n:>4} x {h}")
        print("\n(run on a single file to see its full details)")
    if a.json:
        a.json.write_text(json.dumps(results if len(results) > 1 else results[0], indent=2, default=str))
        print(f"\nwritten {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
