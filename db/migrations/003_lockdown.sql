-- migrate: rerunnable
-- 003_lockdown.sql — the browser must never read tables directly. RLS on, NO policies:
-- Supabase's anon/authenticated roles see nothing; only the backend's DB role (table owner /
-- service role, which bypasses RLS) can read or write.
do $$
declare
    t text;
    tables text[] := array['ingestion_jobs', 'documents', 'job_documents', 'candidates', 'candidate_pii',
                           'chunks', 'job_descriptions', 'search_runs', 'analyses', 'feedback', 'audit_log'];
begin
    foreach t in array tables loop
        execute format('alter table public.%I enable row level security', t);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on table public.%I from anon', t);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on table public.%I from authenticated', t);
        end if;
    end loop;
    revoke execute on function public.hybrid_search_chunks(text, text, vector, integer, real, integer, text[], text, integer) from public;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke execute on function public.hybrid_search_chunks(text, text, vector, integer, real, integer, text[], text, integer) from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke execute on function public.hybrid_search_chunks(text, text, vector, integer, real, integer, text[], text, integer) from authenticated';
    end if;
end
$$;
