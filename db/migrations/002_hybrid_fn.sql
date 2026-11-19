-- migrate: rerunnable
-- 002_hybrid_fn.sql — hybrid (vector + full-text) chunk search merged with Reciprocal Rank Fusion.
--   rrf_score = Σ 1 / (k + rank_i)   with k = 60 by default (plan §7.3)
--
-- Design notes (viva):
--  * Tenant filter first: only the caller's ready resumes that pass the hard filters are scored.
--  * Vector ranking is an EXACT scan over that tenant's chunks (materialised CTE). An HNSW
--    index scan with a WHERE filter post-filters only ef_search (=40) rows and can silently
--    return nothing for one tenant when another tenant's chunks are closer. Exact scan of a
--    few thousand 384-d vectors takes milliseconds. For >100k chunks per tenant switch to
--    pgvector >= 0.8 iterative index scans (hnsw.iterative_scan = relaxed_order).
--  * Empty/blank ts_query => full-text side returns nothing, vector side still works.
--  * Ties are broken by id so results are deterministic.

drop function if exists hybrid_search_chunks(text, text, vector, integer, real, integer, text[], text, integer);

create function hybrid_search_chunks(
    p_owner            text,
    p_ts_query         text,
    p_query_embedding  vector(384),
    p_limit            integer default 200,
    p_min_years        real    default null,
    p_min_edu          integer default null,
    p_required_skills  text[]  default null,
    p_doc_type         text    default 'resume',
    p_rrf_k            integer default 60
) returns table (
    chunk_id       uuid,
    document_id    uuid,
    candidate_id   uuid,
    chunk_index    integer,
    section        text,
    page_no        integer,
    text_scrubbed  text,
    vec_rank       bigint,
    fts_rank       bigint,
    vec_sim        double precision,
    rrf_score      double precision
)
language sql stable
as $$
with eligible as materialized (
    select c.id
    from candidates c
    join documents d on d.id = c.document_id
    where c.owner_id = p_owner
      and d.owner_id = p_owner
      and d.status = 'ready'
      and d.doc_type = p_doc_type
      and (p_min_years is null or c.years_experience >= p_min_years)
      and (p_min_edu is null or c.education_level >= p_min_edu)
      and (p_required_skills is null or cardinality(p_required_skills) = 0 or c.skills @> p_required_skills)
),
pool as materialized (
    select ch.id, ch.embedding, ch.fts
    from chunks ch
    where ch.owner_id = p_owner
      and ch.candidate_id in (select id from eligible)
),
vec as (
    select p.id,
           row_number() over (order by p.embedding <=> p_query_embedding, p.id) as rnk,
           1 - (p.embedding <=> p_query_embedding) as sim
    from pool p
    where p.embedding is not null and p_query_embedding is not null
    order by p.embedding <=> p_query_embedding, p.id
    limit p_limit
),
q as (
    select to_tsquery('english', p_ts_query) as tsq
    where coalesce(btrim(p_ts_query), '') <> ''
),
fts as (
    select p.id,
           row_number() over (order by ts_rank_cd(p.fts, q.tsq) desc, p.id) as rnk
    from pool p
    cross join q
    where p.fts @@ q.tsq
    order by ts_rank_cd(p.fts, q.tsq) desc, p.id
    limit p_limit
),
merged as (
    select coalesce(v.id, f.id) as id,
           v.rnk as vec_rank,
           f.rnk as fts_rank,
           v.sim as vec_sim,
           coalesce(1.0 / (p_rrf_k + v.rnk), 0) + coalesce(1.0 / (p_rrf_k + f.rnk), 0) as rrf_score
    from vec v
    full outer join fts f on v.id = f.id
)
select ch.id, ch.document_id, ch.candidate_id, ch.chunk_index, ch.section, ch.page_no, ch.text_scrubbed,
       m.vec_rank, m.fts_rank, m.vec_sim::double precision, m.rrf_score::double precision
from merged m
join chunks ch on ch.id = m.id
order by m.rrf_score desc, ch.id;
$$;
