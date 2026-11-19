"""Interview-availability e-mails (v1.4): skip-without-email, masking, sending, retries, reply link, tenancy."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from app.config import ConfigError, Settings
from app.services.mailer import MailError, OutboxMailer, OutgoingEmail, mask_email, valid_email
from conftest import A, B, db_conn, drain, upload
from devtools import synthpdf as s


class FakeMailer:
    """Captures e-mails; ``fail`` = list of exceptions raised by the next send() calls."""

    def __init__(self):
        self.sent: list[OutgoingEmail] = []
        self.fail: list[Exception] = []

    def send(self, mail: OutgoingEmail) -> str:
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append(mail)
        return f"<{len(self.sent)}@test>"


def no_email_resume() -> bytes:
    return s.make_pdf([["Rahul Verma", "Phone: +91 9876543210", "", "Summary",
                        "Backend Engineer with 5 years of experience.", "", "Experience",
                        "Backend Engineer, Fictional Corp (2021 - present)",
                        "- Built services using Python, FastAPI and PostgreSQL.", "", "Skills",
                        "Python, FastAPI, PostgreSQL", "", "Education", "B.Tech in Computer Science"]])


@pytest.fixture
def ctx(client_factory):
    mailer = FakeMailer()
    c = client_factory(mailer=mailer)
    people = [s.make_resume(301), s.make_resume(302, role="backend")]
    r = upload(c, [("a.pdf", people[0].pdf), ("b.pdf", people[1].pdf), ("noemail.pdf", no_email_resume())]).json()
    drain(c)
    docs = {d["id"]: d for d in c.get("/api/v1/documents", headers=A).json()["items"]}
    cids = [docs[d]["candidate_id"] for d in r["document_ids"]]
    jd = c.post("/api/v1/job-descriptions", headers=A, json={"title": "Backend Engineer", "text": s.JD_BACKEND}).json()
    return {"c": c, "mailer": mailer, "people": people, "cids": cids, "jd": jd}


def send(c, body, headers=A):
    return c.post("/api/v1/outreach/interview-requests", headers=headers, json=body)


def deliver(c) -> int:
    return c.portal.call(c.app.state.outreach.drain)


def token_of(mail: OutgoingEmail) -> str:
    return re.search(r"#/interview/([A-Za-z0-9_\-]+)", mail.text).group(1)


def test_sends_only_to_candidates_with_an_email_and_skips_the_rest_quietly(ctx):
    c, mailer, people = ctx["c"], ctx["mailer"], ctx["people"]
    r = send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"], "company_name": "Acme Labs",
                 "message": "The interview takes about 45 minutes."})
    assert r.status_code == 202, r.text
    b = r.json()
    assert (b["queued"], b["skipped_no_email"], b["already_invited"]) == (2, 1, 0)
    by = {i["candidate_id"]: i for i in b["items"]}
    assert by[ctx["cids"][2]]["status"] == "skipped_no_email" and by[ctx["cids"][2]]["email_masked"] is None
    assert by[ctx["cids"][0]]["email_masked"] == mask_email(people[0].email)
    assert people[0].email not in r.text and people[1].email not in r.text  # recruiter never sees full addresses
    assert mailer.sent == []  # nothing sent inside the request: the worker does it
    assert deliver(c) == 2
    assert sorted(m.to for m in mailer.sent) == sorted(p.email for p in people)
    m = next(x for x in mailer.sent if x.to == people[0].email)
    assert "Backend Engineer" in m.subject and "Acme Labs" in m.subject
    assert m.text.startswith(f"Hello {people[0].name.split()[0]},")
    assert "preferred interview date and time" in m.text and "45 minutes" in m.text
    assert f"http://localhost:8000/#/interview/{token_of(m)}" in m.text and token_of(m) in m.html
    batch = c.get(f"/api/v1/outreach/batches/{b['batch_id']}", headers=A).json()
    assert batch["done"] and batch["counts"]["sent"] == 2 and all(i["sent_at"] for i in batch["items"])
    audit = c.get("/api/v1/audit", headers=A).json()["items"]
    ev = next(a for a in audit if a["action"] == "outreach_send")
    assert ev["details"]["queued"] == 2 and ev["details"]["skipped_no_email"] == 1
    assert "@" not in str(ev["details"])  # no addresses in the audit log


def test_only_candidates_without_email_is_not_an_error(ctx):
    r = send(ctx["c"], {"jd_id": ctx["jd"]["id"], "candidate_ids": [ctx["cids"][2]]})
    assert r.status_code == 202 and r.json()["queued"] == 0 and r.json()["skipped_no_email"] == 1
    assert deliver(ctx["c"]) == 0


def test_candidate_reply_link_flow(ctx):
    c = ctx["c"]
    send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1], "company_name": "Acme Labs",
             "reply_by": (date.today() + timedelta(days=5)).isoformat()})
    deliver(c)
    tok = token_of(ctx["mailer"].sent[0])
    page = c.get(f"/api/v1/public/interview/{tok}")  # no Authorization header: public
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert page.json()["role_title"] == "Backend Engineer" and page.json()["company_name"] == "Acme Labs"
    assert page.json()["last_reply"] is None and page.json()["expired"] is False
    assert ctx["people"][0].email not in page.text and ctx["people"][0].name not in page.text
    slots = [{"start": "2026-12-03T10:00:00+05:30", "end": "2026-12-03T11:00:00+05:30"},
             {"start": "2026-12-04T15:30:00+05:30"}]
    r = c.post(f"/api/v1/public/interview/{tok}", json={"slots": slots, "timezone": "Asia/Kolkata",
                                                         "note": "Afternoons work best."})
    assert r.status_code == 201, r.text
    assert c.get(f"/api/v1/public/interview/{tok}").json()["last_reply"]["timezone"] == "Asia/Kolkata"
    lst = c.get("/api/v1/outreach/interview-requests", headers=A, params={"jd_id": ctx["jd"]["id"]}).json()["items"]
    assert len(lst) == 1 and lst[0]["reply"]["note"] == "Afternoons work best." and len(lst[0]["reply"]["slots"]) == 2
    replied = c.get("/api/v1/outreach/interview-requests", headers=A, params={"status": "replied"}).json()["items"]
    assert [x["candidate_id"] for x in replied] == [ctx["cids"][0]]
    # validation: offsets required, end after start, 1-5 slots
    bad = [{"slots": [{"start": "2026-12-03T10:00:00"}], "timezone": "Asia/Kolkata"},
           {"slots": [{"start": "2026-12-03T10:00:00+05:30", "end": "2026-12-03T09:00:00+05:30"}], "timezone": "X"},
           {"slots": [], "timezone": "Asia/Kolkata"},
           {"slots": [{"start": "2026-12-03T10:00:00+05:30"}] * 6, "timezone": "Asia/Kolkata"}]
    for body in bad:
        assert c.post(f"/api/v1/public/interview/{tok}", json=body).status_code == 422, body
    assert c.get("/api/v1/public/interview/not-a-real-token").status_code == 404


def test_expired_link_gives_410(ctx, db_url):
    c = ctx["c"]
    send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1]})
    deliver(c)
    tok = token_of(ctx["mailer"].sent[0])
    with db_conn(db_url) as conn:
        conn.execute("update outreach_messages set expires_at = now() - interval '1 day'")
    assert c.get(f"/api/v1/public/interview/{tok}").json()["expired"] is True
    r = c.post(f"/api/v1/public/interview/{tok}", json={"slots": [{"start": "2026-12-03T10:00:00+05:30"}],
                                                         "timezone": "Asia/Kolkata"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "invitation_expired"


def test_already_invited_is_skipped_unless_resend_and_resend_replaces_the_link(ctx):
    c, body = ctx["c"], {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1]}
    send(c, body)
    deliver(c)
    old = token_of(ctx["mailer"].sent[0])
    again = send(c, body).json()
    assert again["queued"] == 0 and again["already_invited"] == 1 and deliver(c) == 0
    resent = send(c, {**body, "resend": True}).json()
    assert resent["queued"] == 1 and deliver(c) == 1 and len(ctx["mailer"].sent) == 2
    new = token_of(ctx["mailer"].sent[1])
    assert new != old
    assert c.get(f"/api/v1/public/interview/{old}").status_code == 404  # the old link stops working
    assert c.get(f"/api/v1/public/interview/{new}").status_code == 200
    assert len(c.get("/api/v1/outreach/interview-requests", headers=A).json()["items"]) == 1


def test_transient_failure_is_retried_and_permanent_failure_is_reported(ctx, db_url):
    c, mailer = ctx["c"], ctx["mailer"]
    mailer.fail = [MailError("connection reset"), MailError("recipient refused", permanent=True)]
    b = send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:2]}).json()
    assert deliver(c) == 2  # first message: transient -> back to queue (later); second: permanent -> failed
    items = {i["status"]: i for i in c.get(f"/api/v1/outreach/batches/{b['batch_id']}", headers=A).json()["items"]}
    assert set(items) == {"queued", "failed"}
    assert items["failed"]["error"] == "recipient refused" and items["queued"]["error"] == "connection reset"
    with db_conn(db_url) as conn:  # pretend the back-off has elapsed
        conn.execute("update outreach_messages set next_attempt_at = now() where status = 'queued'")
    assert deliver(c) == 1 and len(mailer.sent) == 1
    batch = c.get(f"/api/v1/outreach/batches/{b['batch_id']}", headers=A).json()
    assert batch["done"] and batch["counts"] == {"queued": 0, "sending": 0, "sent": 1, "failed": 1}


def test_tenancy_and_validation(ctx):
    c = ctx["c"]
    assert send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1]}, headers=B).status_code == 404
    jd_b = c.post("/api/v1/job-descriptions", headers=B, json={"text": s.JD_BACKEND}).json()
    assert send(c, {"jd_id": jd_b["id"], "candidate_ids": ctx["cids"][:1]}, headers=B).status_code == 404
    b = send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1]}).json()
    assert c.get(f"/api/v1/outreach/batches/{b['batch_id']}", headers=B).status_code == 404
    assert c.get("/api/v1/outreach/interview-requests", headers=B).json()["items"] == []
    assert send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": []}).status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    assert send(c, {"jd_id": ctx["jd"]["id"], "candidate_ids": ctx["cids"][:1], "reply_by": past}).status_code == 422
    assert c.post("/api/v1/outreach/interview-requests", json={}).status_code == 401


def test_disabled_mode_answers_503(client_factory, settings_factory):
    c = client_factory(settings_factory(EMAIL_MODE="disabled"), mailer=FakeMailer())
    jd = c.post("/api/v1/job-descriptions", headers=A, json={"text": s.JD_BACKEND}).json()
    r = send(c, {"jd_id": jd["id"], "candidate_ids": ["00000000-0000-0000-0000-000000000000"]})
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_disabled"


# ------------------------------------------------------------------ units (no database)
def test_outbox_mailer_writes_an_eml_file(tmp_path):
    m = OutboxMailer(Settings(_env_file=None, OUTBOX_DIR=str(tmp_path), EMAIL_REPLY_TO="hr@acme.test"))
    path = m.send(OutgoingEmail(to="a@b.co", subject="Hi", text="Body", html="<p>Body</p>"))
    raw = open(path, "rb").read().decode()
    assert "To: a@b.co" in raw and "Reply-To: hr@acme.test" in raw and "text/html" in raw


def test_email_helpers():
    assert valid_email("priya.sharma+jobs@gmail.com") and not valid_email("priya@") and not valid_email(None)
    assert mask_email("priya.sharma@gmail.com") == "p***@gmail.com"


def test_production_refuses_outbox_and_http_links():
    base = dict(APP_ENV="production", AUTH_MODE="supabase", STORAGE_MODE="supabase", ML_MODE="real",
                SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="a", SUPABASE_SERVICE_KEY="b",
                APP_SECRET="s" * 40)
    with pytest.raises(ConfigError, match="EMAIL_MODE=outbox"):
        Settings(_env_file=None, **base).validate_for_production()
    with pytest.raises(ConfigError, match="PUBLIC_APP_URL"):
        Settings(_env_file=None, **base, EMAIL_MODE="smtp", SMTP_HOST="smtp.x").validate_for_production()
    Settings(_env_file=None, **base, EMAIL_MODE="smtp", SMTP_HOST="smtp.x",
             PUBLIC_APP_URL="https://verihire.example").validate_for_production()
