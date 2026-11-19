"""Test doubles that SIMULATE bad LLM behaviour so the verifier can be measured offline.
(These measure our verification code, not Claude. Run the real model with a key to measure Claude.)"""
from __future__ import annotations

import random

FABRICATED = ["Led a team of 50 engineers at a global technology company",
              "Won the national hackathon three years in a row",
              "Published 12 papers on distributed systems"]


class MutatingLLM:
    """Wraps another LLM; corrupts a fraction of citations the way hallucinating models do."""
    model = "mutating-test-llm"

    def __init__(self, inner, rate: float = 0.3, seed: int = 0):
        self.inner, self.rate, self.rng = inner, rate, random.Random(seed)

    def complete_json(self, **kw) -> dict:
        out = self.inner.complete_json(**kw)
        chunks = (kw.get("payload") or {}).get("chunks", [])
        for s in out.get("skills", []):
            for c in s.get("citations", []):
                if self.rng.random() >= self.rate:
                    continue
                mode = self.rng.choice(["fabricate", "paraphrase", "cross_chunk", "unknown_chunk"])
                words = c["quote"].split()
                if mode == "fabricate":
                    c["quote"] = self.rng.choice(FABRICATED)
                elif mode == "paraphrase" and len(words) >= 3:
                    words[1], words[2] = words[2], words[1]
                    c["quote"] = " ".join(words)
                elif mode == "cross_chunk" and len(chunks) > 1:
                    other = self.rng.choice(chunks)["text"].split("\n")[0]
                    c["quote"] = c["quote"] + " " + other
                else:
                    c["chunk_id"] = "doc_missing:99"
        return out
