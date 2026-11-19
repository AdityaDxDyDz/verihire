-- 004_outreach.sql — interview-availability e-mails to shortlisted candidates (v1.4).
-- One batch = one "send" click by a recruiter; one message per candidate with an e-mail address.
-- Candidates without an e-mail are skipped and never stored here. The recipient address is copied from
-- candidate_pii at send time (same lock-down: only the backend role can read these tables).

create table if not exists outreach_batches (
    id            uuid primary key default gen_random_uuid(),
    owner_id      text not null,
    jd_id         uuid not null references job_descriptions (id) on delete cascade,
    kind          text not null default 'interview_availability',
    company_name  text,
    message       text,                       -- optional note from the recruiter, added to every e-mail
    reply_by      date,
    created_at    timestamptz not null default now()
);
create index if not exists outreach_batches_owner_idx on outreach_batches (owner_id, created_at desc);

create table if not exists outreach_messages (
    id               uuid primary key default gen_random_uuid(),
    batch_id         uuid not null references outreach_batches (id) on delete cascade,
    owner_id         text not null,
    jd_id            uuid not null references job_descriptions (id) on delete cascade,
    candidate_id     uuid not null references candidates (id) on delete cascade,
    recipient        text not null,
    status           text not null default 'queued'
                     check (status in ('queued', 'sending', 'sent', 'failed')),
    attempts         int not null default 0,
    next_attempt_at  timestamptz,
    last_error       text,
    claimed_by       text,
    claimed_at       timestamptz,
    sent_at          timestamptz,
    token_hash       text not null unique,    -- sha256 of the candidate's reply-link token (token itself never stored)
    expires_at       timestamptz not null,
    created_at       timestamptz not null default now(),
    unique (owner_id, jd_id, candidate_id)    -- one invitation per candidate per job (resend reuses the row)
);
create index if not exists outreach_messages_queue_idx on outreach_messages (status, next_attempt_at, created_at);
create index if not exists outreach_messages_owner_jd_idx on outreach_messages (owner_id, jd_id, created_at desc);

create table if not exists interview_replies (
    id            uuid primary key default gen_random_uuid(),
    message_id    uuid not null references outreach_messages (id) on delete cascade,
    owner_id      text not null,
    slots         jsonb not null,             -- [{"start": iso, "end": iso|null}], 1-5, in the candidate's order
    timezone      text not null,
    note          text,
    created_at    timestamptz not null default now()
);
create index if not exists interview_replies_message_idx on interview_replies (message_id, created_at desc);

-- same lock-down as 003: RLS on, no policies, no grants for Supabase's browser roles
do $$
declare
    t text;
begin
    foreach t in array array['outreach_batches', 'outreach_messages', 'interview_replies'] loop
        execute format('alter table public.%I enable row level security', t);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on table public.%I from anon', t);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on table public.%I from authenticated', t);
        end if;
    end loop;
end
$$;
