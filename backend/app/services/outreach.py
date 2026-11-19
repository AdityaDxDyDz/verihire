"""Interview-availability e-mails to shortlisted candidates (v1.4).

Flow
  1. Recruiter selects candidates for a JD and calls POST /outreach/interview-requests.
  2. For each candidate: no valid e-mail in the resume -> skipped quietly (counted, never an error, nothing stored);
     already invited for this JD -> skipped (unless resend=true); otherwise a message row is QUEUED with a fresh
     one-time reply token (only its sha256 is stored).
  3. A background worker sends queued messages (SMTP / outbox), throttled, with retries and backoff.
  4. The candidate opens <PUBLIC_APP_URL>/#/interview/<token> (no login) and submits 1-5 preferred slots, or just
     replies to the e-mail. The recruiter sees replies via GET /outreach/interview-requests?jd_id=...

Privacy: recruiters only ever see a masked address (p***@gmail.com); the full address is used only by the sender.
Every batch writes an audit row (action "outreach_send") with candidate ids and counts, never addresses.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import logging
import os
import secrets
import socket
import uuid
from datetime import UTC, date, datetime, timedelta

from ..config import Settings
from ..db import Database
from ..errors import AppError, not_found, parse_uuid
from .audit import audit
from .mailer import Mailer, MailError, OutgoingEmail, mask_email, valid_email

log = logging.getLogger("verihire.outreach")
STALE_SENDING_S = 300  # a 'sending' row older than this (crashed worker) goes back to the queue


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _first_name(full_name: str | None) -> str | None:
    if not full_name:
        return None
    first = full_name.strip().split()[0].strip(",.")
    return first.capitalize() if first.isalpha() and 1 < len(first) <= 30 else None


def _role_title(jd: dict) -> str:
    return (jd.get("title") or (jd.get("spec") or {}).get("title") or "the open position").strip()


def compose_email(*, to: str, first_name: str | None, role: str, company: str | None, message: str | None,
                  link: str, reply_by: date | None, expires_at: datetime, can_reply_by_email: bool) -> OutgoingEmail:
    """Plain-text + HTML e-mail asking the candidate for preferred interview dates and times."""
    org = company or "our team"
    deadline = (reply_by or expires_at.date()).strftime("%d %B %Y")
    hello = f"Hello {first_name}," if first_name else "Hello,"
    intro = (f"Thank you for your interest. We have reviewed your profile and would like to invite you to an "
             f"interview for the {role} role at {org}.")
    ask = "Please tell us your preferred interview date and time. You can choose up to five options here:"
    alt = "Alternatively, simply reply to this e-mail with two or three options and your time zone."
    due = f"Please respond by {deadline}."
    text_parts = [hello, intro, f"{ask}\n{link}"]
    if can_reply_by_email:
        text_parts.append(alt)
    if message:
        text_parts.append(message.strip())
    text_parts += [due, f"Kind regards,\n{org}"]
    text = "\n\n".join(text_parts) + "\n"

    e = html.escape
    p = '<p style="margin:0 0 14px">{}</p>'.format
    button = (f'<p style="margin:0 0 14px"><a href="{e(link)}" style="background:#2a78d6;color:#ffffff;'
              f'padding:10px 18px;border-radius:6px;text-decoration:none;display:inline-block">'
              f"Choose interview times</a></p>"
              f'<p style="margin:0 0 14px;font-size:12px;color:#555">If the button does not work, open this link: '
              f'<a href="{e(link)}">{e(link)}</a></p>')
    parts = [p(e(hello)), p(e(intro)), p(e(ask)), button]
    if can_reply_by_email:
        parts.append(p(e(alt)))
    if message:
        parts.append(p(e(message.strip()).replace("\n", "<br>")))
    parts += [p(e(due)), p(f"Kind regards,<br>{e(org)}")]
    html_body = '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#111">' + \
        "".join(parts) + "</div>"
    return OutgoingEmail(to=to, subject=f"Interview invitation: {role} at {org} - please share your availability",
                         text=text, html=html_body)


class OutreachService:
    def __init__(self, db: Database, mailer: Mailer, settings: Settings):
        self.db = db
        self.mailer = mailer
        self.s = settings
        self.instance_id = f"{socket.gethostname()[:20]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ recruiter side
    async def create_interview_requests(self, owner_id: str, jd_id: str, candidate_ids: list[str],
                                        message: str | None, company_name: str | None, reply_by: date | None,
                                        resend: bool) -> dict:
        if self.s.EMAIL_MODE == "disabled":
            raise AppError(503, "email_disabled", "Sending e-mail is turned off on this server (EMAIL_MODE=disabled).")
        jid = parse_uuid(jd_id, "Job description")
        jd = await self.db.fetch_one("select id, title, spec from job_descriptions where id = %s and owner_id = %s",
                                     (jid, owner_id))
        if not jd:
            raise not_found("Job description")
        cids = list(dict.fromkeys(parse_uuid(c, "Candidate") for c in candidate_ids))
        rows = await self.db.fetch_all(
            "select c.id, c.label, p.full_name, p.emails from candidates c "
            "left join candidate_pii p on p.candidate_id = c.id where c.owner_id = %s and c.id = any(%s)",
            (owner_id, cids))
        if len(rows) != len(cids):
            raise not_found("Candidate")  # 404, never reveal another tenant's candidate
        by_id = {r["id"]: r for r in rows}
        existing = {r["candidate_id"]: r for r in await self.db.fetch_all(
            "select id, candidate_id, status from outreach_messages where owner_id = %s and jd_id = %s "
            "and candidate_id = any(%s)", (owner_id, jid, cids))}
        now = datetime.now(UTC)
        expires = now + timedelta(days=self.s.OUTREACH_LINK_TTL_DAYS)
        if reply_by:
            expires = max(expires, datetime(reply_by.year, reply_by.month, reply_by.day, 23, 59, tzinfo=UTC))
        items: list[dict] = []
        counts = {"queued": 0, "skipped_no_email": 0, "already_invited": 0}
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "insert into outreach_batches (owner_id, jd_id, company_name, message, reply_by) "
                "values (%s, %s, %s, %s, %s) returning id", (owner_id, jid, company_name, message, reply_by))
            batch_id = (await cur.fetchone())["id"]
            for cid in cids:
                r = by_id[cid]
                email = next((e.strip() for e in (r["emails"] or []) if valid_email(e)), None)
                item = {"candidate_id": str(cid), "label": r["label"], "email_masked": None}
                if not email:
                    counts["skipped_no_email"] += 1
                    items.append({**item, "status": "skipped_no_email"})
                    continue
                item["email_masked"] = mask_email(email)
                if cid in existing and not resend:
                    counts["already_invited"] += 1
                    items.append({**item, "status": "already_invited", "message_id": str(existing[cid]["id"])})
                    continue
                token_hash = _hash(secrets.token_urlsafe(32))  # placeholder; real token issued at send time
                cur = await conn.execute(
                    """insert into outreach_messages (batch_id, owner_id, jd_id, candidate_id, recipient, token_hash,
                                                      expires_at)
                       values (%s, %s, %s, %s, %s, %s, %s)
                       on conflict (owner_id, jd_id, candidate_id) do update
                         set batch_id = excluded.batch_id, recipient = excluded.recipient, status = 'queued',
                             attempts = 0, next_attempt_at = null, last_error = null, claimed_by = null,
                             claimed_at = null, sent_at = null, token_hash = excluded.token_hash,
                             expires_at = excluded.expires_at, created_at = now()
                       returning id""", (batch_id, owner_id, jid, cid, email, token_hash, expires))
                mid = (await cur.fetchone())["id"]
                counts["queued"] += 1
                items.append({**item, "status": "queued", "message_id": str(mid)})
            await audit(self.db, owner_id, "outreach_send", "job_description", str(jid),
                        {"batch_id": str(batch_id), **counts,
                         "candidate_ids": [i["candidate_id"] for i in items if i["status"] == "queued"]}, conn=conn)
        if counts["queued"]:
            self._wake.set()
        return {"batch_id": str(batch_id), "jd_id": str(jid), **counts, "items": items}

    async def list_requests(self, owner_id: str, jd_id: str | None, status: str | None) -> dict:
        params: list = [owner_id]
        where = "m.owner_id = %s"
        if jd_id:
            where += " and m.jd_id = %s"
            params.append(parse_uuid(jd_id, "Job description"))
        if status == "replied":
            where += " and exists (select 1 from interview_replies r where r.message_id = m.id)"
        elif status:
            where += " and m.status = %s"
            params.append(status)
        rows = await self.db.fetch_all(f"""
            select m.id, m.batch_id, m.jd_id, m.candidate_id, c.label, m.recipient, m.status, m.attempts,
                   m.last_error, m.sent_at, m.expires_at, m.created_at,
                   (select json_build_object('slots', r.slots, 'timezone', r.timezone, 'note', r.note,
                                             'created_at', r.created_at)
                      from interview_replies r where r.message_id = m.id order by r.created_at desc limit 1) as reply
            from outreach_messages m join candidates c on c.id = m.candidate_id
            where {where} order by m.created_at desc limit 500""", params)
        return {"items": [self._item(r) for r in rows]}

    async def get_batch(self, owner_id: str, batch_id: str) -> dict:
        bid = parse_uuid(batch_id, "Batch")
        b = await self.db.fetch_one("select * from outreach_batches where id = %s and owner_id = %s", (bid, owner_id))
        if not b:
            raise not_found("Batch")
        rows = await self.db.fetch_all("""
            select m.id, m.batch_id, m.jd_id, m.candidate_id, c.label, m.recipient, m.status, m.attempts,
                   m.last_error, m.sent_at, m.expires_at, m.created_at,
                   (select json_build_object('slots', r.slots, 'timezone', r.timezone, 'note', r.note,
                                             'created_at', r.created_at)
                      from interview_replies r where r.message_id = m.id order by r.created_at desc limit 1) as reply
            from outreach_messages m join candidates c on c.id = m.candidate_id
            where m.batch_id = %s order by c.label""", (bid,))
        items = [self._item(r) for r in rows]
        counts = {k: sum(i["status"] == k for i in items) for k in ("queued", "sending", "sent", "failed")}
        return {"batch_id": str(bid), "jd_id": str(b["jd_id"]), "company_name": b["company_name"],
                "message": b["message"], "reply_by": b["reply_by"], "created_at": b["created_at"],
                "counts": counts, "done": counts["queued"] + counts["sending"] == 0, "items": items}

    @staticmethod
    def _item(r: dict) -> dict:
        return {"message_id": str(r["id"]), "batch_id": str(r["batch_id"]), "jd_id": str(r["jd_id"]),
                "candidate_id": str(r["candidate_id"]), "label": r["label"], "email_masked": mask_email(r["recipient"]),
                "status": r["status"], "attempts": r["attempts"], "error": r["last_error"], "sent_at": r["sent_at"],
                "expires_at": r["expires_at"], "created_at": r["created_at"], "reply": r["reply"]}

    # ------------------------------------------------------------------ candidate side (public, token only)
    async def _by_token(self, token: str) -> dict:
        if not token or len(token) > 200:
            raise not_found("Invitation")
        row = await self.db.fetch_one("""
            select m.id, m.owner_id, m.status, m.expires_at, j.title, j.spec, b.company_name, b.message, b.reply_by
            from outreach_messages m join outreach_batches b on b.id = m.batch_id
            join job_descriptions j on j.id = m.jd_id where m.token_hash = %s""", (_hash(token),))
        if not row or row["status"] != "sent":
            raise not_found("Invitation")
        return row

    async def public_get(self, token: str) -> dict:
        row = await self._by_token(token)
        last = await self.db.fetch_one(
            "select slots, timezone, note, created_at from interview_replies where message_id = %s "
            "order by created_at desc limit 1", (row["id"],))
        return {"role_title": _role_title(row), "company_name": row["company_name"], "message": row["message"],
                "reply_by": row["reply_by"], "expires_at": row["expires_at"],
                "expired": row["expires_at"] < datetime.now(UTC), "last_reply": last}

    async def public_reply(self, token: str, slots: list[dict], timezone: str, note: str | None) -> dict:
        row = await self._by_token(token)
        if row["expires_at"] < datetime.now(UTC):
            raise AppError(410, "invitation_expired", "This invitation link has expired. Please reply to the e-mail.")
        clean = [{"start": s["start"].isoformat(), "end": s["end"].isoformat() if s.get("end") else None} for s in slots]
        r = await self.db.fetch_one(
            "insert into interview_replies (message_id, owner_id, slots, timezone, note) "
            "values (%s, %s, %s::jsonb, %s, %s) returning id, created_at",
            (row["id"], row["owner_id"], json.dumps(clean), timezone, note))
        await audit(self.db, row["owner_id"], "interview_reply", "outreach_message", str(row["id"]),
                    {"n_slots": len(clean)})
        return {"reply_id": str(r["id"]), "created_at": r["created_at"], "slots": clean, "timezone": timezone}

    # ------------------------------------------------------------------ sending worker
    async def start(self) -> None:
        await self.db.execute("update outreach_messages set status = 'queued', claimed_by = null "
                              "where status = 'sending' and claimed_at < now() - make_interval(secs => %s)",
                              (STALE_SENDING_S,))
        if self.s.OUTREACH_WORKERS_ENABLED and self.s.EMAIL_MODE != "disabled":
            self._task = asyncio.create_task(self._loop(), name="outreach-sender")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        gap = 60.0 / self.s.OUTREACH_SEND_PER_MINUTE
        while True:
            try:
                sent = await self.send_one()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("outreach worker error")
                sent = False
            if sent:
                await asyncio.sleep(gap)
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.s.OUTREACH_POLL_INTERVAL_S)

    async def drain(self) -> int:
        """Send everything due now (tests / scripts). Returns the number of messages processed."""
        n = 0
        while await self.send_one():
            n += 1
        return n

    async def send_one(self) -> bool:
        row = await self.db.fetch_one("""
            update outreach_messages m set status = 'sending', claimed_by = %s, claimed_at = now(),
                   attempts = attempts + 1
            where m.id = (select id from outreach_messages
                          where status = 'queued' and (next_attempt_at is null or next_attempt_at <= now())
                          order by created_at for update skip locked limit 1)
            returning m.id, m.owner_id, m.jd_id, m.candidate_id, m.recipient, m.attempts, m.expires_at, m.batch_id""",
                                      (self.instance_id,))
        if not row:
            return False
        ctx = await self.db.fetch_one("""
            select j.title, j.spec, b.company_name, b.message, b.reply_by, p.full_name
            from outreach_batches b join job_descriptions j on j.id = b.jd_id
            left join candidate_pii p on p.candidate_id = %s where b.id = %s""", (row["candidate_id"], row["batch_id"]))
        token = secrets.token_urlsafe(32)  # issued now; only its hash is stored; a retry issues a new one
        link = f"{self.s.PUBLIC_APP_URL.rstrip('/')}/#/interview/{token}"
        mail = compose_email(to=row["recipient"], first_name=_first_name(ctx["full_name"]), role=_role_title(ctx),
                             company=ctx["company_name"], message=ctx["message"], link=link, reply_by=ctx["reply_by"],
                             expires_at=row["expires_at"], can_reply_by_email=bool(self.s.EMAIL_REPLY_TO))
        try:
            await asyncio.wait_for(asyncio.to_thread(self.mailer.send, mail), self.s.SMTP_TIMEOUT_S + 10)
        except (MailError, TimeoutError) as e:
            permanent = getattr(e, "permanent", False) or row["attempts"] >= self.s.OUTREACH_MAX_ATTEMPTS
            backoff = 60 * 2 ** (row["attempts"] - 1)
            await self.db.execute(
                "update outreach_messages set status = %s, last_error = %s, claimed_by = null, "
                "next_attempt_at = now() + make_interval(secs => %s) where id = %s and claimed_by = %s",
                ("failed" if permanent else "queued", str(e)[:300] or type(e).__name__, backoff, row["id"],
                 self.instance_id))
            log.warning("outreach message %s %s: %s", row["id"], "failed" if permanent else "will retry", e)
            return True
        await self.db.execute(
            "update outreach_messages set status = 'sent', sent_at = now(), last_error = null, claimed_by = null, "
            "token_hash = %s where id = %s and claimed_by = %s", (_hash(token), row["id"], self.instance_id))
        return True
