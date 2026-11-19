"""Backend-side citation verification ("never trust the ML layer").

A citation survives only if its quote is found inside the text of a chunk that belongs to the
candidate being analysed. Matching tolerates whitespace runs, curly quotes/dashes and letter case;
the quote returned to the UI is then replaced by the *exact* span of ``text_scrubbed``
(``quote == text_scrubbed[start:end]``), so highlighting is always verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass

from docint.contracts import SkillGapResult

MIN_QUOTE_CHARS = 4
MAX_QUOTE_CHARS = 600
_TRANSLATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-", " ": " ", "•": "-",
}


def normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Normalised text + for each output char the index of the source char it came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True  # also strips leading whitespace
    for i, ch in enumerate(text):
        ch = _TRANSLATE.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        low = ch.lower()
        out.append(low if len(low) == 1 else ch)
        idx.append(i)
        prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalise(text: str) -> str:
    return normalise_with_map(text)[0]


def locate(quote: str, text: str) -> tuple[int, int] | None:
    """(start, end) of ``quote`` inside ``text`` after normalisation, in *original* offsets."""
    q = normalise(quote).strip(" .,;:")
    if len(q) < MIN_QUOTE_CHARS or len(q) > MAX_QUOTE_CHARS:
        return None
    norm, idx = normalise_with_map(text)
    pos = norm.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


@dataclass
class ChunkRef:
    chunk_id: str
    text_scrubbed: str
    page_no: int
    section: str


def verify_and_resolve(result: SkillGapResult, chunks: dict[str, ChunkRef]) -> tuple[SkillGapResult, list[dict]]:
    """Drop citations that are not verbatim in *this candidate's* chunks; downgrade unsupported skills.

    Returns (clean_result, citations_resolved).
    """
    dropped = 0
    resolved: list[dict] = []
    skills = []
    for sk in result.skills:
        kept = []
        for c in sk.citations:
            ch = chunks.get(str(c.chunk_id))
            span = locate(c.quote, ch.text_scrubbed) if ch else None
            if span is None:
                dropped += 1
                continue
            s, e = span
            exact = ch.text_scrubbed[s:e]
            kept.append(c.model_copy(update={"quote": exact}))
            resolved.append({"skill": sk.skill, "chunk_id": ch.chunk_id, "quote": exact,
                             "text_scrubbed": ch.text_scrubbed, "page_no": ch.page_no,
                             "section": ch.section, "start": s, "end": e})
        upd: dict = {"citations": kept}
        if sk.status in ("present", "partial") and not kept:
            upd["status"] = "missing"
            upd["reasoning"] = "No verifiable evidence in the resume (claim removed by citation check)."
        skills.append(sk.model_copy(update=upd))
    clean = result.model_copy(update={
        "skills": skills,
        "dropped_unverified_claims": int(result.dropped_unverified_claims or 0) + dropped,
    })
    return clean, resolved
