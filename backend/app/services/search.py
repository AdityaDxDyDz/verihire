"""Search: JD -> queries -> embed -> hybrid SQL (RRF) -> per-candidate evidence -> rerank -> explained results."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from docint.contracts import CandidateEvidence, JobSpec, RankedCandidate, ResumeProfile, RetrievedChunk

from ..adapters.ml import MLAdapter
from ..config import Settings
from ..db import Database, vector_literal
from ..errors import not_found, parse_uuid
from . import jobdesc
from .audit import audit
from .csv_export import results_to_csv

log = logging.getLogger("verihire.search")
NO_REQUIRED_SKILLS_WARNING = (
    "no_required_skills: no required skills were recognised in this job description, so candidates are ranked by "
    "text relevance only. Add the key skills under Edit requirements for a skill-based ranking."
)

try:  # Track C's matcher: the same implied-skill rules the reranker uses (fastapi => python)
    from docint.rank.skillmatch import expand_implied as _expand_implied
except ImportError:  # pragma: no cover - Track C absent: literal names only
    _expand_implied = None


def skills_with_implied(skills: list[str]) -> set[str]:
    """Skills a candidate counts as having for the matched/missing chips. Must agree with the reranker's coverage
    feature, otherwise the #1 candidate can show "python missing" while being ranked as fully qualified."""
    have = {s.lower() for s in skills}
    if _expand_implied is not None:
        have |= {s.lower() for s in _expand_implied(skills)}
    return have


_TERM_SPLIT = re.compile(r"[|&!()\s:*<>'\"\\,;/+.-]+")


def sanitise_ts_query(q: str | None, max_terms: int = 25) -> str:
    """Make any string safe for ``to_tsquery``: ``a | b | c`` with [a-z0-9_] terms only."""
    terms: list[str] = []
    for part in _TERM_SPLIT.split((q or "").lower()):
        t = re.sub(r"[^a-z0-9_]", "", part)
        if t and t not in terms:
            terms.append(t)
    return " | ".join(terms[:max_terms])


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


class SearchService:
    def __init__(self, db: Database, ml: MLAdapter, settings: Settings, cache_size: int = 256):
        self.db = db
        self.ml = ml
        self.s = settings
        self._qcache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_size = cache_size

    async def _embed_query(self, text: str) -> list[float]:
        key = (self.ml.embedder_name, text)
        if key in self._qcache:
            self._qcache.move_to_end(key)
            return self._qcache[key]
        vec = await self.ml.embed_query(text)
        self._qcache[key] = vec
        if len(self._qcache) > self._cache_size:
            self._qcache.popitem(last=False)
        return vec

    async def search(self, owner_id: str, req) -> dict:
        t_all = time.perf_counter()
        timings: dict[str, float] = {}
        warnings: list[str] = []
        jd_row = await jobdesc.get_row(self.db, owner_id, req.jd_id)
        spec: JobSpec = jobdesc.spec_from_row(jd_row)

        t = time.perf_counter()
        queries = await self.ml.build_queries(spec)
        ts_query = sanitise_ts_query(queries.ts_query)
        timings["queries_ms"] = _ms(t)

        t = time.perf_counter()
        qvec = await self._embed_query(queries.dense_query or spec.raw_text[:1000] or (spec.title or "candidate"))
        timings["embed_ms"] = _ms(t)

        f = req.filters
        req_skills = [s.strip().lower() for s in f.required_skills if s and s.strip()] or None
        t = time.perf_counter()
        rows = await self.db.fetch_all(
            "select * from hybrid_search_chunks(%s, %s, %s::vector, %s::int, %s::real, %s::int, %s::text[], 'resume', %s::int)",
            (owner_id, ts_query, vector_literal(qvec), self.s.RETRIEVAL_LIMIT, f.min_years, f.min_education,
             req_skills, self.s.RRF_K))
        timings["retrieve_ms"] = _ms(t)

        # ---- aggregate chunks per candidate (rows arrive sorted by rrf desc)
        by_cand: dict[str, list[dict]] = {}
        for r in rows:
            by_cand.setdefault(str(r["candidate_id"]), []).append(r)
        agg = []
        for cid, chs in by_cand.items():
            scores = [c["rrf_score"] for c in chs]
            agg.append((cid, sum(scores[:3]), max(scores), len(chs)))
        agg.sort(key=lambda a: (-a[1], a[0]))
        pool = agg[: self.s.RERANK_POOL]

        results: list[dict] = []
        reranker_version = self.ml.reranker_version
        if pool:
            cand_rows = await self.db.fetch_all(
                "select c.id, c.document_id, c.label, c.profile, d.suspicious_hidden_text "
                "from candidates c join documents d on d.id = c.document_id "
                "where c.owner_id = %s and c.id = any(%s)", (owner_id, [uuid.UUID(p[0]) for p in pool]))
            info = {str(r["id"]): r for r in cand_rows}
            evidence: list[CandidateEvidence] = []
            for cid, rsum, rmax, n in pool:
                if cid not in info:
                    continue
                profile = ResumeProfile.model_validate(info[cid]["profile"])
                chunks = [RetrievedChunk(
                    chunk_id=str(c["chunk_id"]), document_id=str(c["document_id"]), candidate_id=cid,
                    chunk_index=c["chunk_index"], section=c["section"], page_no=c["page_no"],
                    text_scrubbed=c["text_scrubbed"], rrf_score=float(c["rrf_score"]),
                    vec_rank=c["vec_rank"], fts_rank=c["fts_rank"],
                    vec_sim=None if c["vec_sim"] is None else float(c["vec_sim"])) for c in by_cand[cid]]
                evidence.append(CandidateEvidence(candidate_id=cid, profile=profile, chunks=chunks,
                                                  rrf_sum_top3=float(rsum), rrf_max=float(rmax), n_chunks_hit=n))

            t = time.perf_counter()
            if not spec.required_skills:
                # The reranker was trained only on JDs with required skills; with none, its coverage features are 0
                # for everyone and every score collapses to ~0. Rank by text relevance instead and say so.
                ranked = await self._rerank(spec, evidence, warnings, use_model=False)
                reranker_version = "retrieval-order-no-required-skills"
                warnings.append(NO_REQUIRED_SKILLS_WARNING)
            else:
                ranked = await self._rerank(spec, evidence, warnings)
            if "reranker_failed" in warnings:
                reranker_version = "retrieval-order-fallback"
            elif self.ml.reranker_is_fallback and spec.required_skills:
                warnings.append("reranker_model_missing: ranked by retrieval score (train the model: docint.rank.train)")
            timings["rerank_ms"] = _ms(t)

            ev_by_id = {e.candidate_id: e for e in evidence}
            names: dict[str, str | None] = {}
            if not req.blind:
                top_ids = [uuid.UUID(r.candidate_id) for r in ranked[: req.top_k]]
                pii_rows = await self.db.fetch_all(
                    "select p.candidate_id, p.full_name from candidate_pii p join candidates c on c.id = p.candidate_id "
                    "where c.owner_id = %s and p.candidate_id = any(%s)", (owner_id, top_ids))
                names = {str(r["candidate_id"]): r["full_name"] for r in pii_rows}
            for rc in ranked[: req.top_k]:
                results.append(self._result(spec, rc, ev_by_id[rc.candidate_id], info[rc.candidate_id],
                                            names.get(rc.candidate_id) if not req.blind else None))
        else:
            n_ready = await self.db.fetch_one(
                "select count(*) as n from documents where owner_id = %s and status = 'ready'", (owner_id,))
            warnings.append("no_ready_documents" if n_ready["n"] == 0 else "no_matching_candidates")

        stale = await self.db.fetch_one(
            "select count(*) as n from documents where owner_id = %s and status = 'ready' "
            "and embedder is distinct from %s", (owner_id, self.ml.embedder_name))
        if stale["n"]:
            warnings.append(f"stale_embeddings: {stale['n']} documents were embedded with a different model; "
                            "re-process them with POST /documents/retry")

        timings["total_ms"] = _ms(t_all)
        search_id = str(uuid.uuid4())
        response = {
            "search_id": search_id, "results": results, "timings": timings, "warnings": warnings,
            "reranker_version": reranker_version, "embedder": self.ml.embedder_name,
            "job_spec": spec.model_dump(), "blind": req.blind, "jd_id": str(jd_row["id"]),
            "created_at": datetime.now(UTC).isoformat(),
        }
        await self.db.execute(
            "insert into search_runs (id, owner_id, jd_id, request, response) values (%s, %s, %s, %s::jsonb, %s::jsonb)",
            (search_id, owner_id, jd_row["id"], req.model_dump_json(), json.dumps(response, default=str)))
        if not req.blind:
            await audit(self.db, owner_id, "search_reveal", "search", search_id,
                        {"candidates": [r["candidate_id"] for r in results]})
        return response

    async def _rerank(self, spec: JobSpec, evidence: list[CandidateEvidence], warnings: list[str],
                      use_model: bool = True) -> list[RankedCandidate]:
        ranked: list[RankedCandidate] = []
        if use_model:
            try:
                ranked = await self.ml.rerank(spec, evidence)
            except Exception:  # noqa: BLE001 - search must still answer
                log.exception("reranker failed; falling back to retrieval order")
                warnings.append("reranker_failed")
                ranked = []
        known = {e.candidate_id for e in evidence}
        seen: set[str] = set()
        ordered: list[RankedCandidate] = []
        for r in sorted(ranked, key=lambda r: (r.rank, -r.match_score)):
            if r.candidate_id in known and r.candidate_id not in seen:
                ordered.append(r)
                seen.add(r.candidate_id)
        if ranked and len(seen) < len(known):
            warnings.append("reranker_incomplete")
        # anything the reranker did not return keeps retrieval order, scored relative to the best
        top = max((e.rrf_sum_top3 for e in evidence), default=0) or 1.0
        for e in evidence:  # evidence is already in retrieval order
            if e.candidate_id not in seen:
                ordered.append(RankedCandidate(candidate_id=e.candidate_id,
                                               match_score=round(100 * e.rrf_sum_top3 / top, 2), rank=1,
                                               feature_values={"rrf_sum_top3": e.rrf_sum_top3}))
                seen.add(e.candidate_id)
        return [r.model_copy(update={"rank": i + 1, "match_score": max(0.0, min(100.0, float(r.match_score)))})
                for i, r in enumerate(ordered)]

    @staticmethod
    def _result(spec: JobSpec, rc: RankedCandidate, ev: CandidateEvidence, row: dict, display_name: str | None) -> dict:
        prof = ev.profile
        have = skills_with_implied(prof.skills)
        req = [s.lower() for s in spec.required_skills]
        pref = [s.lower() for s in spec.preferred_skills]
        return {
            "candidate_id": ev.candidate_id,
            "document_id": str(row["document_id"]),
            "label": row["label"],
            "match_score": round(rc.match_score, 2),
            "rank": rc.rank,
            "years_experience": float(prof.years_experience),
            "education_level": int(prof.education_level),
            "titles": prof.titles[:5],
            "top_skills": prof.skills[:10],
            "matched_required": [s for s in req if s in have],
            "missing_required": [s for s in req if s not in have],
            "matched_preferred": [s for s in pref if s in have],
            "missing_preferred": [s for s in pref if s not in have],
            "suspicious_hidden_text": bool(row["suspicious_hidden_text"]),
            "top_chunks": [{"chunk_id": c.chunk_id, "page_no": c.page_no, "section": c.section,
                            "text_scrubbed": c.text_scrubbed} for c in ev.chunks[:3]],
            "explanation": {"feature_contributions": rc.feature_contributions, "feature_values": rc.feature_values,
                            "cross_encoder_score": rc.cross_encoder_score},
            "retrieval": {"rrf_sum_top3": round(ev.rrf_sum_top3, 6), "n_chunks_hit": ev.n_chunks_hit},
            "display_name": display_name,
        }

    async def get_run(self, owner_id: str, search_id: str) -> dict:
        sid = parse_uuid(search_id, "Search")
        row = await self.db.fetch_one("select response from search_runs where id = %s and owner_id = %s", (sid, owner_id))
        if not row:
            raise not_found("Search")
        return row["response"]

    async def export_csv(self, owner_id: str, search_id: str) -> str:
        run = await self.get_run(owner_id, search_id)
        return results_to_csv(run.get("results", []))
