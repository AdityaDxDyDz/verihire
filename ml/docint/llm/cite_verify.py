"""E9: citation verification. The product's main promise: every displayed citation is a verbatim
substring of the cited chunk's text_scrubbed. We NEVER trust the LLM here.

Steps per skill: verify each quote against its OWN chunk (so cross-chunk quotes fail), replace the
quote with the exact original substring (so it is verbatim even if the LLM changed quotes or
whitespace), drop what cannot be verified, then downgrade statuses that no longer have evidence.
"""
from __future__ import annotations

from docint.contracts import Citation, SkillAssessment, SkillGapResult
from docint.rank.skillmatch import expand_implied, find_skills

MAX_QUOTE_WORDS = 25
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
           " ": " ", "•": "-"}
RANK = {"missing": 0, "partial": 1, "present": 2}


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise quotes/dashes/whitespace; map each normalised char to its original index."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        ch = _QUOTES.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def locate_quote(quote: str, chunk_text: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of the quote inside chunk_text, or None."""
    nq, _ = _normalize_with_map(quote.strip())
    nq = nq.strip()
    if not nq:
        return None
    nt, idx = _normalize_with_map(chunk_text)
    pos = nt.find(nq)
    if pos < 0:
        return None
    start = idx[pos]
    end = idx[pos + len(nq) - 1] + 1
    return start, end


def verify_skill_gap(result: SkillGapResult, chunk_text_by_id: dict[str, str], required: list[str],
                     preferred: list[str]) -> SkillGapResult:
    dropped = 0
    by_skill: dict[str, SkillAssessment] = {}
    for a in result.skills:
        skill = a.skill.strip().lower()
        status = a.status if a.status in RANK else "missing"
        good: list[Citation] = []
        for c in a.citations:
            text = chunk_text_by_id.get(c.chunk_id)
            if text is None or not c.quote.strip() or len(c.quote.split()) > MAX_QUOTE_WORDS:
                dropped += 1
                continue
            span = locate_quote(c.quote, text)
            if span is None:
                dropped += 1
                continue
            good.append(Citation(chunk_id=c.chunk_id, quote=text[span[0]:span[1]]))  # verbatim original slice
        reasoning = a.reasoning
        if status in ("present", "partial"):
            if not good:  # claim without verified evidence
                status, reasoning = "missing", (reasoning + " [no verifiable evidence]").strip()
                dropped += 1
            else:
                # relevance guard: a verified quote that never mentions the skill is not evidence for it
                mentions = any(skill in expand_implied(find_skills(c.quote)) or skill in c.quote.lower() for c in good)
                if not mentions:
                    chunk_mentions = any(skill in expand_implied(find_skills(chunk_text_by_id[c.chunk_id])) for c in good)
                    new = "partial" if chunk_mentions else "missing"  # never keep "present" on an irrelevant quote
                    if RANK[new] < RANK[status]:
                        dropped += 1
                        reasoning = (reasoning + " [quote does not mention the skill]").strip()
                    status = new
        if status == "missing":
            good = good if good else []
        by_skill[skill] = SkillAssessment(skill=skill, status=status, reasoning=reasoning, citations=good)

    ordered: list[SkillAssessment] = []
    for s in list(dict.fromkeys(list(required) + list(preferred))):
        ordered.append(by_skill.get(s.lower()) or SkillAssessment(
            skill=s.lower(), status="missing", reasoning="No evidence found in the resume text.", citations=[]))
    return SkillGapResult(summary=result.summary, skills=ordered, strengths=result.strengths[:6], risks=result.risks[:6],
                          interview_questions=result.interview_questions[:5], dropped_unverified_claims=dropped,
                          model=result.model, prompt_version=result.prompt_version)
