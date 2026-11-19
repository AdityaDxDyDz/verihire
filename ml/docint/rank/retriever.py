"""E2: offline reference retriever (no DB). Mirrors the SQL hybrid_search_chunks + the
backend's candidate aggregation (plan 7.3): RRF score = sum 1/(60 + rank), then group by
candidate with rrf_sum_top3 / rrf_max / n_chunks_hit. Modes: bm25 | vector | hybrid.
Used for baselines (E0 = bm25, E1 = vector, E2 = hybrid) and for training-data generation.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from docint.contracts import Chunk, JobSpec, ResumeProfile, RetrievedChunk, CandidateEvidence
from docint.rank.queries import build_queries, tokenize

RRF_K = 60


@dataclass
class CorpusDoc:
    doc_id: str
    profile: ResumeProfile
    chunks: list[Chunk]
    embeddings: np.ndarray  # (n_chunks, dim)
    truth: dict


def load_corpus(path: str | Path) -> list[CorpusDoc]:
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            docs.append(CorpusDoc(
                doc_id=r["doc_id"], profile=ResumeProfile(**r["profile"]),
                chunks=[Chunk(**c) for c in r["chunks"]],
                embeddings=np.asarray(r["embedding"], dtype=np.float32), truth=r.get("truth", {})))
    return docs


class BM25:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(t) for t in docs_tokens]
        self.dl = np.array([len(t) for t in docs_tokens], dtype=float)
        self.avgdl = self.dl.mean() if len(self.dl) else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs_tokens)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query_terms: list[str]) -> np.ndarray:
        s = np.zeros(len(self.tf))
        for t in set(query_terms):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(t, 0)
                if f:
                    s[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
        return s


def _rank_order(scores: np.ndarray) -> np.ndarray:
    """Descending order that is identical on every OS/BLAS build (v1.2): scores rounded to 6 decimals (float32 dot
    products differ in the last bits between Windows and Linux), ties broken by index with a STABLE sort
    (np.argsort's default quicksort is not stable)."""
    return np.argsort(-np.round(np.asarray(scores, dtype=np.float64), 6), kind="stable")


class ReferenceRetriever:
    def __init__(self, docs: list[CorpusDoc], embedder, top_n_each: int = 100):
        self.docs = {d.doc_id: d for d in docs}
        self.embedder = embedder
        self.top_n = top_n_each
        self.keys: list[tuple[str, int]] = []  # (doc_id, chunk position)
        mats, toks = [], []
        for d in docs:
            for i, c in enumerate(d.chunks):
                self.keys.append((d.doc_id, i))
                toks.append(tokenize(c.text_scrubbed))
            mats.append(d.embeddings)
        self.matrix = np.vstack(mats)
        self.bm25 = BM25(toks)

    def search(self, job: JobSpec, mode: str = "hybrid") -> list[CandidateEvidence]:
        assert mode in ("bm25", "vector", "hybrid")
        q = build_queries(job)
        n = len(self.keys)
        vec_rank: dict[int, int] = {}
        fts_rank: dict[int, int] = {}
        sims = np.zeros(n)
        if mode in ("vector", "hybrid"):
            qv = np.asarray(self.embedder.embed_query(q.dense_query), dtype=np.float32)
            sims = self.matrix @ qv
            for r, idx in enumerate(_rank_order(sims)[: self.top_n], start=1):
                vec_rank[int(idx)] = r
        if mode in ("bm25", "hybrid"):
            terms = [t.strip() for t in q.ts_query.split("|") if t.strip()]
            sc = self.bm25.scores(terms)
            order = _rank_order(sc)[: self.top_n]
            for r, idx in enumerate([i for i in order if sc[i] > 0], start=1):
                fts_rank[int(idx)] = r
        hit = set(vec_rank) | set(fts_rank)
        by_doc: dict[str, list[RetrievedChunk]] = defaultdict(list)
        for idx in hit:
            doc_id, pos = self.keys[idx]
            d = self.docs[doc_id]
            score = (1 / (RRF_K + vec_rank[idx]) if idx in vec_rank else 0) + (1 / (RRF_K + fts_rank[idx]) if idx in fts_rank else 0)
            by_doc[doc_id].append(RetrievedChunk(
                chunk_id=f"{doc_id}:{d.chunks[pos].chunk_index}", chunk=d.chunks[pos], rrf_score=score,
                vec_rank=vec_rank.get(idx), fts_rank=fts_rank.get(idx),
                vec_sim=float(sims[idx]) if mode != "bm25" else None))
        out = []
        for doc_id, chs in by_doc.items():
            chs.sort(key=lambda c: -c.rrf_score)
            top3 = [c.rrf_score for c in chs[:3]]
            out.append(CandidateEvidence(candidate_id=doc_id, profile=self.docs[doc_id].profile, chunks=chs,
                                         rrf_sum_top3=float(sum(top3)), rrf_max=float(top3[0]), n_chunks_hit=len(chs)))
        out.sort(key=lambda c: (-c.rrf_sum_top3, c.candidate_id))
        return out
