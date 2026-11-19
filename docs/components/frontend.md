# Component fact-sheet: Frontend (owner: Aditya Kadam)

## What it does
A single-page app for recruiters: upload resumes, describe a role, get a ranked shortlist, and inspect *why* each person ranked where they did, with every claim tied to a highlighted sentence in the resume.

## Screens
1. **Login**: mock / dev / Supabase modes (decided by config).
2. **Library**: drag-and-drop PDF upload with real progress, pipeline status per file, filters, search, pagination, original-PDF viewer, retry failed, delete.
3. **Job & search**: paste JD → extracted requirements (editable chips, years, education, responsibilities) → search panel (top-k, hard filters, blind mode).
4. **Results**: ranked matrix, sort, "only full coverage", compare selection (2–4), CSV export, thumbs feedback, warnings and stub banners.
5. **Candidate dossier (drawer)**: audited identity reveal, facts, "why this score" diverging bars (contributions sum to score − 50), on-demand skill-gap analysis with highlighted evidence papers, strengths/risks/interview questions.
6. **Compare**: side-by-side skills grid.

## Design decisions (and why)
- **No framework, no build step.** Zero install risk on demo day; every file is readable in a viva. Tradeoff: manual DOM code, mitigated by small `h()` helper and unit-tested pure logic.
- **`innerHTML` is banned** (lint test). Resume text is untrusted; everything uses `textContent` via `h()`.
- **Highlights come from server offsets** (`start/end` into `text_scrubbed`), and the UI refuses to mark a range whose text ≠ quote (`usableCitations`). It shows "could not be matched" instead of wrong highlights.
- **`match_score` is shown as a relative score**, never a probability.
- **Blind by default**; identity reveal needs confirmation and is audited server-side.
- **Mock is a real contract client**: the same `api.js` runs on a mock transport. Switching to the backend changes one line.
- **Accessibility**: keyboard-operable, focus trap in modals/drawer, Esc closes, status never colour-only, reduced-motion respected, dark mode.

## Contract questions for Aditya Raj (backend)
1. `GET /documents` must return `counts` with keys `all, ready, processing, failed, needs_ocr`; `status=processing` = any in-flight stage.
2. Upload where **no** file is valid: mock returns `400 no_valid_files`. Is that the real behaviour? (UI handles both that and a 200 with only `rejected`.)
3. Filenames may contain names. The library is not blind; only ranking views are.
4. 429 should send `Retry-After`; UI shows it.
5. `pdf-url` must return a short-lived URL that the browser can open in an iframe (CORS / `X-Frame-Options` must allow).
6. Static hosting: backend serves the `frontend/` folder and generates `config.js` from env.

## Viva questions you should be able to answer
- Why vanilla JS? What would React have bought, and cost?
- How do you guarantee a highlight is correct? (offset + quote equality, server verification, UI re-check.)
- What stops an XSS via a malicious resume? (no innerHTML, lint, textContent, CSP in deployment.)
- Why a mock transport instead of mocking `fetch`? (identical code path, stateful demo.)
- How does token refresh avoid races? (single in-flight refresh promise; 401 → refresh once → retry once.)
- What is the match score? (relative ranking, contributions sum to score − 50.)

## Test evidence
75 node tests (format, highlight, validate, router, state, auth, api, mock, security lint), 18 pytest (fixtures↔models, offsets verbatim, mock server), Playwright e2e covering login → library → JD → search → results → dossier → analysis highlights → compare → mobile no-overflow.
