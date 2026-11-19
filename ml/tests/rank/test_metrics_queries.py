"""Expected values below were worked out BY HAND before the code was run."""
import math
import random

import pytest

from docint.contracts import JobSpec
from docint.eval.metrics import mrr, ndcg_at_k, precision_at_k, recall_at_k
from docint.rank.queries import VALID_TSQUERY, build_queries

REL = {"a": 3, "b": 0, "c": 2, "d": 1}


def test_ndcg_by_hand():
    # ranked a,b,c: DCG = 7/log2(2) + 0 + 3/log2(4) = 7 + 1.5 = 8.5
    # ideal 3,2,1:  IDCG = 7 + 3/log2(3) + 1/log2(4) = 7 + 1.8928 + 0.5 = 9.3928
    assert ndcg_at_k(["a", "b", "c"], REL, 3) == pytest.approx(8.5 / (7 + 3 / math.log2(3) + 0.5), abs=1e-9)
    assert ndcg_at_k(["a", "c", "d", "b"], REL, 4) == pytest.approx(1.0)  # ideal order


def test_mrr_recall_precision_by_hand():
    assert mrr(["b", "d", "c", "a"], REL) == pytest.approx(1 / 3)  # first label>=2 is c at rank 3
    assert mrr(["b", "d"], REL) == 0.0
    assert recall_at_k(["a", "b"], REL, 2) == pytest.approx(0.5)   # relevant = {a, c}; found a
    assert precision_at_k(["a", "b"], REL, 2) == pytest.approx(0.5)


def test_ts_query_examples():
    q = build_queries(JobSpec(title="Dev", raw_text="x", required_skills=["c++", "node.js", "machine learning"],
                              preferred_skills=["ci/cd"]))
    assert q.ts_query == "cpp | nodejs | machine | learning | cicd"


def test_ts_query_fuzz_always_valid_and_capped():
    rng = random.Random(0)
    alphabet = "abcXYZ 019_+#./-'\"();:|&!<>\\\n\té中"
    for _ in range(500):
        skills = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 20))) for _ in range(rng.randint(0, 40))]
        q = build_queries(JobSpec(raw_text="x", required_skills=skills[:20], preferred_skills=skills[20:]))
        assert VALID_TSQUERY.match(q.ts_query), repr(q.ts_query)
        assert len(q.ts_query.split(" | ")) <= 25 if q.ts_query else True
