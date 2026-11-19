"""Rule-based JD parser: the fallback when the LLM fails/has no key, and the StubLLM brain."""
from __future__ import annotations

import re

from docint.rank.skillmatch import find_skills

PREFERRED = re.compile(r"nice to have|preferred|bonus|good to have|a plus|optional|desirable", re.I)
REQUIRED = re.compile(r"requirements?|must have|required|qualifications|what you.?ll need|you have", re.I)
RESPONS = re.compile(r"responsibilit|what you.?ll do|duties", re.I)
# "3+ years", "2-5 years", "3 to 5 yrs" -> the MINIMUM (first number)
YEARS = re.compile(r"(\d+(?:\.\d+)?)(?:\s*(?:-|\u2013|to)\s*\d+(?:\.\d+)?)?\s*\+?\s*(?:years?|yrs?)", re.I)
EDU = [(4, re.compile(r"ph\.?d|doctorate", re.I)), (3, re.compile(r"master|m\.?tech|m\.?sc|\bmba\b", re.I)),
       (2, re.compile(r"bachelor|b\.?tech|b\.?e\b|b\.?sc|degree", re.I)), (1, re.compile(r"diploma", re.I))]


HEADING_WORDS = (r"(?:key |minimum |basic |preferred |desired |required )?(?:requirements?|qualifications?|skills"
                 r"(?: required)?|responsibilities|duties|nice to have|good to have|preferred|bonus|must have|"
                 r"what you.?ll (?:do|need)|you have|the role)(?:\s*&\s*(?:skills?\s*&?\s*)?qualifications?)?")
INLINE_HEADING = re.compile(rf"^(?P<h>{HEADING_WORDS})\s*:\s*(?P<rest>\S.*)$", re.I)
SPLIT_HEADING = re.compile(rf"\s+(?=(?:{HEADING_WORDS})\s*:)", re.I)
SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\-*\u2022])")


def _heading_mode(line: str) -> str | None:
    """Classify common section labels with or without punctuation and prefixes."""
    label = re.sub(r"[^a-z0-9]+", " ", line.casefold()).strip()
    if not label or len(label.split()) > 8:
        return None
    if PREFERRED.search(label):
        return "preferred"
    if RESPONS.search(label):
        return "resp"
    if REQUIRED.search(label) or re.search(r"\bskills\b", label):
        return "required"
    return None


def _lines(text: str) -> list[str]:
    """Line list with long prose exploded: JDs copied from job boards / CSV datasets are often ONE paragraph, which
    used to be read as a giant title (=> no skills). Inline headings ("Requirements: python, sql") go on their own
    line and long lines are split into sentences."""
    out: list[str] = []
    for raw in text.replace("\r", "").split("\n"):
        ln = raw.strip()
        if len(ln) <= 80:
            out.append(ln)
            continue
        for part in SPLIT_HEADING.split(ln):
            out.extend(s.strip() for s in SENTENCE.split(part) if s.strip())
    expanded: list[str] = []
    for ln in out:  # "Requirements: 3+ years python" -> "Requirements:" + "3+ years python"
        m = INLINE_HEADING.match(ln)
        if m and not ln.startswith(("-", "*", "\u2022")):
            expanded += [m.group("h") + ":", m.group("rest")]
        else:
            expanded.append(ln)
    return expanded


def _is_title(line: str) -> bool:
    return len(line) <= 80 and len(line.split()) <= 10 and not YEARS.search(line)


def rule_based_job_spec(text: str) -> dict:
    lines = _lines(text)
    first = next((l for l in lines if l), None)
    title = first.rstrip(".") if first and _is_title(first) else None
    mode = "required"
    req: list[str] = []
    pref: list[str] = []
    resp: list[str] = []
    years = None
    edu = 0
    title_seen = False
    has_headings = any(l and not l.startswith(("-", "*", "•", "�")) and len(l) < 80
                       and _heading_mode(l.rstrip(":")) is not None for l in lines)
    in_body = not has_headings  # with headings, intro text before the first one is not mined for skills
    for ln in lines:
        if not ln:
            continue
        if not title_seen:  # a short first line is the title: never mine it for skills (prose is mined)
            title_seen = True
            if title is not None:
                continue
        is_bullet = ln.startswith(("-", "*", "•", "�", "▪", "·"))
        if not is_bullet and len(ln) < 80:
            heading_mode = _heading_mode(ln.rstrip(":"))
        else:
            heading_mode = None
        if heading_mode is not None:
            in_body = True
            mode = heading_mode
            continue
        if not in_body:
            continue
        body = ln.lstrip("-*•▪�· \t").strip()
        if mode == "resp":
            resp.append(body)
            continue
        line_mode = "preferred" if (mode == "preferred" or PREFERRED.search(body)) else "required"
        for s in find_skills(body):
            (pref if line_mode == "preferred" else req).append(s)
        if line_mode == "required":  # "a master's degree is a plus" is not a minimum
            m = YEARS.search(body)
            if m and years is None:
                years = float(m.group(1))
            for level, pat in EDU:
                if pat.search(body):
                    edu = max(edu, level)
    req = list(dict.fromkeys(req))
    pref = [s for s in dict.fromkeys(pref) if s not in req]
    return {"title": title, "required_skills": req, "preferred_skills": pref,
            "min_years": years, "min_education": edu or None, "responsibilities": resp[:8]}
