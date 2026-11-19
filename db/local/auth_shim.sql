-- auth_shim.sql — LOCAL ONLY. Recreates the Supabase roles so 003_lockdown.sql behaves exactly
-- as in production. Applied by:  python scripts/migrate.py --local-shim
-- Authentication itself is NOT emulated here: locally the backend uses AUTH_MODE=dev tokens.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin bypassrls;
    end if;
end
$$;
