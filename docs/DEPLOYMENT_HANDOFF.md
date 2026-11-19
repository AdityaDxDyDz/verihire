# Deployment hand-off (owner: Aditya Kadam)

Status: **not yet built**. Frontend side is ready; this is the plan and what each piece needs.

## Shape
One Docker container: FastAPI serves `/api/v1/*` and the static `frontend/` folder. Primary host: Hugging Face Docker Space (`sdk: docker`, `app_port: 7860`). Database: Supabase Postgres via the **pooler** connection string.

## `frontend/config.js` (generated at container start from env)
```js
window.VERIHIRE_CONFIG = { mode: "supabase", apiBaseUrl: "", supabaseUrl: "<SUPABASE_URL>", supabaseAnonKey: "<SUPABASE_ANON_KEY>" };
```
Generate with a tiny entrypoint script (`envsubst` or Python). **Never** put the service-role key in it. Anon key + URL are public by design.

## Secrets (never in git)
`DATABASE_URL`, `SUPABASE_SERVICE_KEY`, LLM API key → Space secrets. `.env` is in `.gitignore`.

## Checklist
- [ ] Dockerfile (python:3.12-slim, install backend, copy `frontend/`, entrypoint writes config.js)
- [ ] Security headers on static: CSP `default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; frame-src <storage origin>; img-src 'self' data:`
- [ ] Supabase: enable email auth, set redirect URL to the Space URL
- [ ] Free Supabase projects pause after ~1 week idle: open the app before the demo
- [ ] Smoke test on the deployed URL: `/api/v1/health`, login, upload, search
- [ ] Demo fallback: `?mock=1` works with zero backend. Keep that URL ready.


## v1.4: e-mail settings are now required in production
The app refuses to start in production with `EMAIL_MODE=outbox` (the dev default). Set either
`EMAIL_MODE=disabled`, or `EMAIL_MODE=smtp` + `SMTP_HOST` / `SMTP_USERNAME` / `SMTP_PASSWORD` + `EMAIL_FROM` +
`PUBLIC_APP_URL=https://<your app>` (candidate reply links). Details: `docs/components/outreach.md`.
