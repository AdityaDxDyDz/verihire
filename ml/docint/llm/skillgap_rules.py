"""Rule-based skill-gap answer used by StubLLM (offline). Honest by construction: it only quotes
lines that really contain the skill. Also hosts ObedientLLM, a test double that OBEYS
prompt-injection text, used to prove the verifier neutralises injected claims."""
from __future__ import annotations

from docint.rank.skillmatch import expand_implied, find_skills

EVIDENCE_SECTIONS = {"experience", "projects"}


def _best_line(text: str, skill: str) -> str | None:
    for ln in text.splitlines():
        if skill in expand_implied(find_skills(ln)):
            words = ln.strip().lstrip("-• ").split()
            return " ".join(words[:25])  # a prefix of whole words is still a verbatim substring
    return None


def rule_based_skill_gap(payload: dict) -> dict:
    skills: list[str] = payload["skills"]
    chunks: list[dict] = payload["chunks"]  # {chunk_id, section, text}
    out, present, partial, missing = [], [], [], []
    for s in skills:
        best = None  # (rank, chunk_id, quote, status)
        for ch in chunks:
            q = _best_line(ch["text"], s)
            if not q:
                continue
            status = "present" if ch["section"] in EVIDENCE_SECTIONS else "partial"
            rank = 2 if status == "present" else 1
            if best is None or rank > best[0]:
                best = (rank, ch["chunk_id"], q, status)
        if best:
            out.append({"skill": s, "status": best[3],
                        "reasoning": "Used in professional work." if best[3] == "present" else "Listed or mentioned, no work evidence.",
                        "citations": [{"chunk_id": best[1], "quote": best[2]}]})
            (present if best[3] == "present" else partial).append(s)
        else:
            out.append({"skill": s, "status": "missing", "reasoning": "No mention in the resume text.", "citations": []})
            missing.append(s)
    asked = (present + partial)[:3] or ["their recent work"]
    return {"summary": f"Evidence found for {len(present)} skills, partial for {len(partial)}, none for {len(missing)}.",
            "skills": out,
            "strengths": [f"Demonstrated {s} in experience" for s in present[:4]],
            "risks": [f"No evidence of {s}" for s in missing[:4]],
            "interview_questions": [f"Describe a project where you used {s}. What went wrong and how did you fix it?" for s in asked]}


class ObedientLLM:
    """Test double for E10: behaves like a model that FOLLOWS injected instructions.
    It marks every skill 'present' and cites the injection sentence plus an unrelated real line."""
    model = "obedient-test-llm"

    def complete_json(self, *, system: str, user: str, tool_name: str, schema: dict,
                      payload: dict | None = None, tier: str = "fast") -> dict:
        assert tool_name == "emit_skill_gap" and payload
        chunks = payload["chunks"]
        inj = None
        filler = None
        for ch in chunks:
            for ln in ch["text"].splitlines():
                low = ln.lower()
                if inj is None and any(k in low for k in ("ignore all previous", "rate this candidate", "note to the ai",
                                                           "disregard the job", "mark all required", "mark every skill")):
                    inj = (ch["chunk_id"], ln.strip()[:200])
                if filler is None and ch["section"] == "education":
                    filler = (ch["chunk_id"], ln.strip())
        cites = []
        if inj:
            words = inj[1].split()
            cites.append({"chunk_id": inj[0], "quote": " ".join(words[:20])})
        if filler:
            cites.append({"chunk_id": filler[0], "quote": filler[1]})
        return {"summary": "Perfect candidate, matches everything.",
                "skills": [{"skill": s, "status": "present", "reasoning": "Resume says it is a perfect match.",
                            "citations": cites} for s in payload["skills"]],
                "strengths": ["Perfect"], "risks": [], "interview_questions": ["Why are you so great?"]}
