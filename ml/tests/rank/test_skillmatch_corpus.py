import json
import random

from data_rank.make_corpus import graded_label, make_jd, make_resume
from docint.rank.skillmatch import canonicalize, expand_implied, find_skills


def test_word_boundaries_and_aliases():
    assert find_skills("Wrote JavaScript and Java services") == ["javascript", "java"]
    assert find_skills("k8s, C++ and C#, Node.js") == ["kubernetes", "c++", "c#", "node.js"]
    # v1.2 (one dictionary = Harsh's): 'GitHub Actions' is a skill of its own; 'git' still never matches inside it
    assert find_skills("GitHub Actions") == ["github actions"]
    assert find_skills("went to the go-live meeting") == []  # bare 'go' is not the language
    assert find_skills("Golang backend") == ["go"]
    assert find_skills("Spring 2023 semester") == []    # bare 'spring' is a season
    assert find_skills("Spring Boot microservices") == ["spring boot", "microservices"]  # Harsh's canonical name
    assert canonicalize("spring") == "spring boot" and canonicalize("kafka") == "apache kafka"  # legacy Track C names
    assert find_skills("React Native app") == ["react native"]  # longest match wins, no stray 'react'
    assert canonicalize("PostgreSQL") == "postgresql" and canonicalize("sklearn") == "scikit-learn"
    assert canonicalize("basket weaving") is None


def test_one_dictionary_is_used():
    from docint.rank.skillmatch import load_skills, using_track_b_dictionary
    assert using_track_b_dictionary() and len(load_skills()) > 300  # never the silent 74-skill fallback


def test_implications():
    assert expand_implied({"django"}) == {"django", "python"}
    assert "javascript" in expand_implied({"react"}) and "docker" in expand_implied({"kubernetes"})


def test_generator_is_deterministic_and_synonym_hides_keyword():
    a = make_resume(5, random.Random(11), kind="synonym")
    b = make_resume(5, random.Random(11), kind="synonym")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    for seed in range(40):
        d = make_resume(seed, random.Random(seed), kind="synonym")
        if "python" in d["truth"]["true_skills"]:
            assert "python" not in d["profile"]["skills"]  # keyword is invisible, framework names are not
            assert set(d["profile"]["skills"]) & {"django", "fastapi", "flask"}


def test_chunk_offsets_and_no_name_in_scrubbed_text():
    d = make_resume(2, random.Random(4), kind="normal")
    for c in d["chunks"]:
        assert c["text_original"] == c["text_scrubbed"]
        assert c["char_end"] - c["char_start"] == len(c["text_original"])
    assert d["truth"]["name"] not in " ".join(c["text_scrubbed"] for c in d["chunks"])


def test_label_rubric_by_hand():
    jd = {"required": ["python", "django", "sql", "docker"], "min_years": 3.0, "min_education": 2}
    full = {"true_skills": ["python", "django", "sql", "docker"], "years": 4, "education": 2}
    assert graded_label(jd, full) == 3
    assert graded_label(jd, {**full, "years": 1}) == 2            # coverage 100% but years short, edu ok
    # django implies python, but docker is missing: coverage 3/4 = 0.75 -> grade 2 (not 3)
    assert graded_label(jd, {**full, "true_skills": ["django", "sql"], "years": 5}) == 2
    # with docker as well, django => python makes coverage 4/4 -> grade 3
    assert graded_label(jd, {**full, "true_skills": ["django", "sql", "docker"], "years": 5}) == 3
    assert graded_label(jd, {**full, "true_skills": ["sql", "docker"], "years": 0, "education": 1}) == 1  # 50% coverage
    assert graded_label(jd, {**full, "true_skills": ["react"]}) == 0
