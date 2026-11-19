# Interview-availability e-mails — backend contract for the frontend (v1.4)

**Owner:** Raj (backend, done). **Frontend:** Kadam (to build). Real example responses:
`docs/contracts/examples_backend/outreach_*.json` and `public_*.json` (validated against the models on every test run).
OpenAPI: `/docs` → tag **outreach**.

## What it does

1. The recruiter shortlists candidates for a job (e.g. ticks rows on the results page) and clicks
   **"Request interview times"**.
2. The backend e-mails every selected candidate **who has an e-mail address in their resume**, asking for their
   preferred interview date and time. Candidates **without an e-mail are skipped quietly**: not an error, nothing is
   sent or stored; they are counted as `skipped_no_email`. A candidate already invited for the same job is skipped
   (`already_invited`) unless the recruiter chooses **resend**.
3. Sending runs in the background (throttled, 3 attempts with back-off). The UI polls the batch for progress.
4. The e-mail contains a private link **`<PUBLIC_APP_URL>/#/interview/<token>`**. The candidate opens it **without
   logging in**, sees the role and company, and submits **1–5 preferred slots**. They may also simply reply to the
   e-mail when `EMAIL_REPLY_TO` is set.
5. The recruiter sees delivery status and the candidate's latest reply per job.

Privacy: the recruiter UI only ever receives a **masked address** (`p***@gmail.com`), never the full one, and the
candidate's page shows no personal data. Every send is written to the audit log (`outreach_send`; candidate ids and
counts, no addresses), and so is every reply (`interview_reply`).

## The e-mail candidates receive

```
Subject: Interview invitation: Backend Engineer at Acme Labs - please share your availability

Hello Aman,

Thank you for your interest. We have reviewed your profile and would like to invite you to an interview for the
Backend Engineer role at Acme Labs.

Please tell us your preferred interview date and time. You can choose up to five options here:
https://<your-app>/#/interview/z2PpJPIbzMGhahpitrUYlslmrsKrt2anM2vSX282jGM

Alternatively, simply reply to this e-mail with two or three options and your time zone.   (only if EMAIL_REPLY_TO)

The interview is a 45-minute video call.                                                   (recruiter's message)

Please respond by 01 December 2026.

Kind regards,
Acme Labs
```
Plain text + HTML (with a "Choose interview times" button). The role is the JD title; the greeting uses the
candidate's first name when the resume parser found one.

## Recruiter endpoints (Bearer token, as everywhere else)

### `POST /api/v1/outreach/interview-requests` → **202**
```json
{ "jd_id": "…", "candidate_ids": ["…", "…"],          // 1-100, the shortlist
  "company_name": "Acme Labs",                          // optional, ≤120 chars; e-mail says "our team" without it
  "message": "The interview is a 45-minute video call.",// optional, ≤2000 chars, appended to every e-mail
  "reply_by": "2026-12-01",                             // optional date, not in the past
  "resend": false }                                     // optional: e-mail again candidates already invited for this JD
```
Response (`outreach_create.json`):
```json
{ "batch_id": "…", "jd_id": "…", "queued": 2, "skipped_no_email": 1, "already_invited": 0,
  "items": [ { "candidate_id": "…", "label": "Candidate 9A1F3C", "status": "queued",
               "email_masked": "a***@example.com", "message_id": "…" },
             { "candidate_id": "…", "label": "Candidate 51B0D2", "status": "skipped_no_email",
               "email_masked": null, "message_id": null } ] }
```
`items[].status`: `queued` | `skipped_no_email` | `already_invited`.

### `GET /api/v1/outreach/batches/{batch_id}` — progress of one send (`outreach_batch.json`)
`counts` = `{queued, sending, sent, failed}`; **`done: true`** when nothing is queued or sending. Poll every ~2 s
until `done` (a retry after a temporary SMTP error waits 1, 2, 4 minutes, so a batch can stay "not done" a while; it
is fine to stop polling after ~30 s and show "still sending").

### `GET /api/v1/outreach/interview-requests?jd_id=…&status=…` — the "Interviews" list (`outreach_list.json`)
Latest first, max 500. `status` filter: `queued | sending | sent | failed | replied`. Each item:
```json
{ "message_id": "…", "batch_id": "…", "jd_id": "…", "candidate_id": "…", "label": "Candidate 9A1F3C",
  "email_masked": "a***@example.com", "status": "sent", "attempts": 1, "error": null,
  "sent_at": "2026-11-19T12:41:07Z", "expires_at": "2026-12-03T12:41:05Z", "created_at": "…",
  "reply": { "slots": [ {"start": "2026-11-25T10:00:00+05:30", "end": "2026-11-25T11:00:00+05:30"},
                        {"start": "2026-11-26T15:00:00+05:30", "end": null} ],
             "timezone": "Asia/Kolkata", "note": "Mornings preferred.", "created_at": "…" } }
```
`status` per message: `queued` → `sending` → `sent`, or `failed` (`error` says why, e.g. "recipient refused").
`reply` is `null` until the candidate answers; it is always the **latest** reply (candidates may change their mind).

## Candidate endpoints (NO login — the token in the URL is the credential)

### `GET /api/v1/public/interview/{token}` (`public_invitation.json`)
```json
{ "role_title": "Backend Engineer", "company_name": "Acme Labs", "message": "The interview is a 45-minute video call.",
  "reply_by": "2026-12-01", "expires_at": "2026-12-03T12:41:05Z", "expired": false,
  "last_reply": null }            // or their previous answer: show it and let them update it
```
### `POST /api/v1/public/interview/{token}` → **201** (`public_reply.json`)
```json
{ "slots": [ {"start": "2026-11-25T10:00:00+05:30", "end": "2026-11-25T11:00:00+05:30"},
             {"start": "2026-11-26T15:00:00+05:30"} ],          // 1-5; end optional, after start
  "timezone": "Asia/Kolkata",                                     // IANA name, shown to the recruiter
  "note": "Mornings preferred." }                                 // optional, ≤1000 chars
```
**Every `start`/`end` must carry a UTC offset** (`+05:30`, `Z`); a bare local time is rejected (422).

## Errors (same `{"error": {"code", "message"}}` envelope)

| HTTP | code | When | UI |
|---|---|---|---|
| 404 | `not_found` | unknown / other user's JD, candidate or batch; **public: invalid token, or a link replaced by a resend** | public page: "This link is no longer valid — please use the latest e-mail or reply to it." |
| 410 | `invitation_expired` | public POST after `expires_at` | show `expired` state (GET also returns `expired: true`) |
| 422 | `validation_error` | empty `candidate_ids`, >100, past `reply_by`, bad slots | show message |
| 429 | `rate_limited` | >10 sends/min per recruiter; >20 public calls/min per IP | `Retry-After` header |
| 503 | `email_disabled` | server runs with `EMAIL_MODE=disabled` | hide / disable the button (`/health` can be extended if needed) |

## Suggested UI (Kadam)

1. **Results page** — enable a **"Request interview times (n)"** button when rows are ticked (reuse the compare
   selection). Dialog: company name (remember last value in localStorage), message (textarea), reply-by date,
   "Send again to already-invited candidates" checkbox. On 202 show a summary toast:
   *"Sending to 2 candidates · 1 skipped (no e-mail in resume) · 0 already invited"* — list skipped labels in a
   details disclosure, never as an error. Then poll the batch and update to *"Sent to 2"* / *"1 failed"*.
2. **Interviews panel per JD** (tab on the JD or results page): table of `GET /outreach/interview-requests?jd_id=` —
   candidate label (links to the drawer), masked e-mail, status chip (queued/sending = grey, sent = blue,
   failed = red with `error` as tooltip, replied = green), sent time, and the reply: each slot formatted in the
   **recruiter's** time zone with the candidate's zone shown (`Tue 25 Nov, 10:00–11:00 IST (candidate: Asia/Kolkata)`),
   plus the note. "Replied" filter = `status=replied`. A "Resend" action = POST with `resend: true` for that candidate.
3. **Public page `#/interview/:token`** — must be reachable **without signing in** (skip the auth guard and the app
   header for this route). Show role, company, message, deadline. Form: up to 5 rows of date + start time + optional
   end time; time-zone select defaulting to `Intl.DateTimeFormat().resolvedOptions().timeZone`. If `last_reply` exists,
   pre-fill it and say "You can update your answer". Success state: "Thanks — the recruiter has your times."
   Converting a local date/time in the chosen zone to an ISO string with offset:
   ```js
   // date "2026-11-25", time "10:00", tz "Asia/Kolkata" -> "2026-11-25T10:00:00+05:30"
   function toIsoWithOffset(date, time, tz) {
     const guess = new Date(`${date}T${time}:00Z`);
     const asTz = new Date(guess.toLocaleString("en-US", { timeZone: tz }));
     const asUtc = new Date(guess.toLocaleString("en-US", { timeZone: "UTC" }));
     const off = Math.round((asTz - asUtc) / 60000);               // minutes east of UTC
     const sign = off >= 0 ? "+" : "-", a = Math.abs(off);
     return `${date}T${time}:00${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
   }
   ```
   Do not log or send the token anywhere else (it is the candidate's credential); the backend already answers with
   `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
4. **Mock mode** (`?mock=1`): add the five example files to the fixtures and routes in `mock.js`
   (`POST/GET /outreach/...`, `GET/POST /public/interview/:token`), e.g. a "mock-token" that returns
   `public_invitation.json`.

## Running it

| Setting (`.env`) | Default | Notes |
|---|---|---|
| `EMAIL_MODE` | `outbox` | `outbox` writes `.eml` files to `OUTBOX_DIR` (`.data/outbox/`) — **nothing is sent**; open them with Outlook / Thunderbird / any text editor. `smtp` sends for real. `disabled` → 503. **Production must be `smtp` or `disabled`.** |
| `EMAIL_FROM` | `VeriHire <no-reply@localhost>` | sender shown to candidates |
| `EMAIL_REPLY_TO` | empty | e.g. the recruiter's mailbox; enables the "or simply reply" sentence |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `SMTP_SSL` | `587`, STARTTLS | Gmail: `smtp.gmail.com`, 587, your address + an **app password**. Port 465: `SMTP_SSL=true SMTP_STARTTLS=false` |
| `PUBLIC_APP_URL` | `http://localhost:8000` | base of the candidate link; production: the https address of the app |
| `OUTREACH_SEND_PER_MINUTE` | 30 | throttle (stay under the provider's limit) |
| `OUTREACH_LINK_TTL_DAYS` | 14 | link lifetime (at least until `reply_by`) |
| `OUTREACH_MAX_ATTEMPTS` | 3 | retries for temporary SMTP errors (1, 2, 4 min back-off) |

Try it locally (outbox mode, nothing leaves the laptop): start the server, open `/docs`, authorize with
`dev:you@example.com`, `POST /outreach/interview-requests` with a JD id and candidate ids (from
`GET /documents` → `candidate_id`), then open the newest file in `.data/outbox/` and the link inside it
(`http://localhost:8000/#/interview/<token>` — until the page exists, call `GET /api/v1/public/interview/<token>`).

**Never use `EMAIL_MODE=smtp` with real (e.g. Kaggle) resumes** — those addresses belong to real people.

Database: `db/migrations/004_outreach.sql` (`outreach_batches`, `outreach_messages`, `interview_replies`; RLS locked
down like the rest). Tests: `backend/tests/test_outreach.py` (11).
