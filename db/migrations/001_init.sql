-- 001_init.sql — VeriHire core schema (owner: Raj; frozen at Gate G1)
-- Every table carries owner_id (the authenticated user id) and every backend query filters on it.

create extension if not exists vector;

create table if not exists ingestion_jobs (
    id          uuid primary key default gen_random_uuid(),
    owner_id    text not null,
    kind        text not null default 'upload' check (kind in ('upload', 'retry')),
    created_at  timestamptz not null default now()
);
create index if not exists ingestion_jobs_owner_idx on ingestion_jobs (owner_id, created_at desc);

create table if not exists documents (
    id                     uuid primary key default gen_random_uuid(),
    owner_id               text not null,
    filename               text not null,
    doc_type               text not null default 'resume' check (doc_type in ('resume')),
    storage_path           text not null,
    size_bytes             integer not null check (size_bytes > 0),
    content_sha256         text not null,
    page_count             integer,
    status                 text not null default 'uploaded'
                           check (status in ('uploaded', 'extracting', 'embedding', 'ready', 'failed', 'needs_ocr')),
    error_code             text,
    error_message          text,
    attempts               integer not null default 0,
    claimed_by             text,
    claimed_at             timestamptz,
    heartbeat_at           timestamptz,
    pipeline_version       text,
    embedder               text,                -- embedding model used (detects stale vectors)
    suspicious_hidden_text boolean not null default false,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    unique (owner_id, content_sha256)
);
create index if not exists documents_owner_idx on documents (owner_id, created_at desc);
create index if not exists documents_queue_idx on documents (created_at) where status = 'uploaded';
create index if not exists documents_inflight_idx on documents (heartbeat_at) where status in ('extracting', 'embedding');

-- which documents belong to which job (a retry creates a new job for the same documents)
create table if not exists job_documents (
    job_id      uuid not null references ingestion_jobs (id) on delete cascade,
    document_id uuid not null references documents (id) on delete cascade,
    primary key (job_id, document_id)
);
create index if not exists job_documents_doc_idx on job_documents (document_id);

create table if not exists candidates (
    id                uuid primary key default gen_random_uuid(),
    owner_id          text not null,
    document_id       uuid not null unique references documents (id) on delete cascade,
    label             text not null,
    profile           jsonb not null,
    skills            text[] not null default '{}',
    years_experience  real not null default 0,
    education_level   smallint not null default 0,
    created_at        timestamptz not null default now()
);
create index if not exists candidates_owner_idx on candidates (owner_id);
create index if not exists candidates_skills_idx on candidates using gin (skills);

-- PII lives in its own table; only GET /candidates/{id}?reveal=true reads it (audited)
create table if not exists candidate_pii (
    candidate_id uuid primary key references candidates (id) on delete cascade,
    full_name    text,
    emails       text[] not null default '{}',
    phones       text[] not null default '{}',
    urls         text[] not null default '{}',
    other        jsonb not null default '[]'
);

create table if not exists chunks (
    id             uuid primary key default gen_random_uuid(),
    owner_id       text not null,
    document_id    uuid not null references documents (id) on delete cascade,
    candidate_id   uuid not null references candidates (id) on delete cascade,
    chunk_index    integer not null,
    section        text not null default 'other',
    page_no        integer not null,
    char_start     integer not null,
    char_end       integer not null,
    text_scrubbed  text not null,
    token_count    integer not null default 0,
    embedding      vector(384),
    fts            tsvector generated always as
                   (to_tsvector('english', coalesce(section, '') || ' ' || text_scrubbed)) stored,
    unique (document_id, chunk_index)
);
create index if not exists chunks_candidate_idx on chunks (candidate_id);
create index if not exists chunks_owner_idx on chunks (owner_id);
create index if not exists chunks_fts_idx on chunks using gin (fts);
create index if not exists chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);

create table if not exists job_descriptions (
    id          uuid primary key default gen_random_uuid(),
    owner_id    text not null,
    title       text,
    raw_text    text not null,
    spec        jsonb not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists job_descriptions_owner_idx on job_descriptions (owner_id, updated_at desc);

create table if not exists search_runs (
    id          uuid primary key default gen_random_uuid(),
    owner_id    text not null,
    jd_id       uuid references job_descriptions (id) on delete set null,
    request     jsonb not null,
    response    jsonb not null,
    created_at  timestamptz not null default now()
);
create index if not exists search_runs_owner_idx on search_runs (owner_id, created_at desc);

create table if not exists analyses (
    id                  uuid primary key default gen_random_uuid(),
    owner_id            text not null,
    jd_id               uuid not null references job_descriptions (id) on delete cascade,
    candidate_id        uuid not null references candidates (id) on delete cascade,
    prompt_version      text not null,
    spec_hash           text not null,
    result              jsonb not null,
    citations_resolved  jsonb not null,
    created_at          timestamptz not null default now(),
    unique (owner_id, jd_id, candidate_id, prompt_version, spec_hash)
);

create table if not exists feedback (
    id            uuid primary key default gen_random_uuid(),
    owner_id      text not null,
    search_id     uuid not null references search_runs (id) on delete cascade,
    candidate_id  uuid not null references candidates (id) on delete cascade,
    rating        smallint not null check (rating in (-1, 1)),
    note          text,
    created_at    timestamptz not null default now(),
    unique (owner_id, search_id, candidate_id)
);

create table if not exists audit_log (
    id           bigserial primary key,
    owner_id     text not null,
    action       text not null,
    target_type  text,
    target_id    text,
    details      jsonb not null default '{}',
    request_id   text,
    created_at   timestamptz not null default now()
);
create index if not exists audit_log_owner_idx on audit_log (owner_id, created_at desc);
