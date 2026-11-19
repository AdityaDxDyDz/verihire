"""Hand-written expectations first; the verifier must satisfy them."""
import random

from docint.contracts import Citation, SkillAssessment, SkillGapResult
from docint.llm.cite_verify import locate_quote, verify_skill_gap

CH1 = "EXPERIENCE\nBackend Engineer at Zenotech (2 yrs)\n- Built APIs using FastAPI; improved latency by 30%."
CH2 = "SKILLS\nSkills: Python, Docker,   Redis"
TEXTS = {"d:0": CH1, "d:1": CH2}


def run(assessments, required=("fastapi", "docker", "kubernetes")):
    return verify_skill_gap(SkillGapResult(skills=assessments), TEXTS, list(required), [])


def A(skill, status, *cites):
    return SkillAssessment(skill=skill, status=status, reasoning="r", citations=[Citation(chunk_id=c, quote=q) for c, q in cites])


def by(res):
    return {a.skill: a for a in res.skills}


def test_exact_quote_kept():
    r = run([A("fastapi", "present", ("d:0", "Built APIs using FastAPI"))])
    assert by(r)["fastapi"].status == "present"
    assert by(r)["fastapi"].citations[0].quote == "Built APIs using FastAPI"
    assert r.dropped_unverified_claims == 0


def test_smart_quotes_and_whitespace_normalised_to_verbatim_slice():
    r = run([A("docker", "present", ("d:1", "Skills: Python, Docker, Redis"))])  # nbsp + single space
    q = by(r)["docker"].citations[0].quote
    assert q in CH2 and q == "Skills: Python, Docker,   Redis"  # original spacing restored => verbatim
    assert by(r)["docker"].status == "present"


def test_fabricated_quote_dropped_and_status_downgraded():
    r = run([A("fastapi", "present", ("d:0", "Led a team of 50 engineers"))])
    assert by(r)["fastapi"].citations == [] and by(r)["fastapi"].status == "missing"
    assert r.dropped_unverified_claims == 2  # one bad citation + one unsupported status


def test_truncated_with_ellipsis_rejected_but_plain_prefix_accepted():
    assert run([A("fastapi", "present", ("d:0", "Built APIs using Fast..."))]).skills[0].status == "missing"
    assert run([A("fastapi", "present", ("d:0", "Built APIs using Fast"))]).skills[0].citations  # still a substring


def test_cross_chunk_quote_rejected():
    r = run([A("docker", "present", ("d:1", "Python, Docker, Redis Built APIs using FastAPI"))])
    assert by(r)["docker"].status == "missing"
    r2 = run([A("docker", "present", ("d:0", "latency by 30%. SKILLS Skills: Python, Docker"))])
    assert by(r2)["docker"].status == "missing"


def test_quote_longer_than_25_words_rejected():
    long_chunk = {"d:9": " ".join(f"w{i}" for i in range(40)) + " docker"}
    res = verify_skill_gap(SkillGapResult(skills=[A("docker", "present", ("d:9", " ".join(f"w{i}" for i in range(26))))]),
                           long_chunk, ["docker"], [])
    assert res.skills[0].status == "missing"


def test_unknown_chunk_id_dropped():
    assert by(run([A("docker", "present", ("nope:1", "Docker"))]))["docker"].status == "missing"


def test_quote_must_mention_skill_to_keep_present():
    # real, verbatim, but about something else: cannot prove FastAPI
    r = run([A("fastapi", "present", ("d:0", "Backend Engineer at Zenotech (2 yrs)"))])
    assert by(r)["fastapi"].status == "partial"  # the chunk does mention FastAPI, quote does not
    r2 = run([A("kubernetes", "present", ("d:1", "Skills: Python, Docker,   Redis"))])
    assert by(r2)["kubernetes"].status == "missing"  # nothing about kubernetes anywhere


def test_missing_skills_are_filled_in_and_order_follows_job():
    r = run([A("fastapi", "present", ("d:0", "FastAPI"))])
    assert [a.skill for a in r.skills] == ["fastapi", "docker", "kubernetes"]
    assert by(r)["docker"].status == "missing" and by(r)["docker"].citations == []


def test_property_random_substrings_accepted_random_mutations_rejected():
    rng = random.Random(42)
    for _ in range(300):
        text = rng.choice([CH1, CH2])
        i = rng.randrange(0, len(text) - 3)
        j = rng.randrange(i + 1, min(len(text), i + 60) + 1)
        frag = text[i:j]
        if not frag.strip():
            continue
        span = locate_quote(frag, text)
        assert span is not None and text[span[0]:span[1]].split() == frag.split()
        k = rng.randrange(0, len(frag) + 1)
        mutated = frag[:k] + "§§" + frag[k:]  # insert a char that exists nowhere in the text
        assert locate_quote(mutated, text) is None
