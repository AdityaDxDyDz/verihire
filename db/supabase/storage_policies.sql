-- storage_policies.sql — SUPABASE ONLY. Run once in the production project (SQL editor, or
--   python scripts/migrate.py --supabase-storage).
-- Private bucket for original resume PDFs. NO storage policies are created on purpose:
-- the browser never touches storage; the backend uploads/downloads with the service key and
-- hands out short-lived signed URLs (GET /api/v1/documents/{id}/pdf-url).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('resumes', 'resumes', false, 10485760, array['application/pdf'])
on conflict (id) do update
    set public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;
