"""Outgoing e-mail. Standard library only (smtplib / email), so no new dependency.

EMAIL_MODE
  outbox    development: every e-mail is written as an .eml file to OUTBOX_DIR (open it with any mail app);
            nothing leaves the machine. The default, so a laptop never mails real (e.g. Kaggle) people by accident.
  smtp      real sending through SMTP_HOST (Gmail / Outlook / SendGrid / SES SMTP ...). Required in production.
  disabled  the outreach endpoints answer 503 email_disabled.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from ..config import Settings

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def valid_email(s: str | None) -> bool:
    return bool(s) and len(s) <= 254 and EMAIL_RE.match(s.strip()) is not None


def mask_email(s: str) -> str:
    """'priya.sharma@gmail.com' -> 'p***@gmail.com' (enough for a recruiter to recognise a typo, nothing more)."""
    local, _, domain = s.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str | None = None


class MailError(RuntimeError):
    """Sending failed; ``permanent`` = retrying will not help (bad address, auth refused)."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings

    def _build(self, mail: OutgoingEmail) -> EmailMessage:
        m = EmailMessage()
        m["From"] = self.s.EMAIL_FROM
        m["To"] = mail.to
        m["Subject"] = mail.subject
        m["Date"] = formatdate(localtime=False)
        m["Message-ID"] = make_msgid(domain=self.s.EMAIL_FROM.rsplit("@", 1)[-1].strip("> ") or "verihire.local")
        if self.s.EMAIL_REPLY_TO:
            m["Reply-To"] = self.s.EMAIL_REPLY_TO
        m.set_content(mail.text)
        if mail.html:
            m.add_alternative(mail.html, subtype="html")
        return m

    def send(self, mail: OutgoingEmail) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class OutboxMailer(Mailer):
    """Writes <OUTBOX_DIR>/<timestamp>-<id>.eml. Returns the file path."""

    def send(self, mail: OutgoingEmail) -> str:
        folder = Path(self.s.OUTBOX_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
        path.write_bytes(bytes(self._build(mail)))
        return str(path)


class SmtpMailer(Mailer):
    def send(self, mail: OutgoingEmail) -> str:
        msg = self._build(mail)
        try:
            if self.s.SMTP_SSL:
                server = smtplib.SMTP_SSL(self.s.SMTP_HOST, self.s.SMTP_PORT, timeout=self.s.SMTP_TIMEOUT_S,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self.s.SMTP_HOST, self.s.SMTP_PORT, timeout=self.s.SMTP_TIMEOUT_S)
            with server:
                if self.s.SMTP_STARTTLS and not self.s.SMTP_SSL:
                    server.starttls(context=ssl.create_default_context())
                if self.s.SMTP_USERNAME:
                    server.login(self.s.SMTP_USERNAME, self.s.SMTP_PASSWORD)
                refused = server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise MailError(f"recipient refused: {e.recipients}", permanent=True) from None
        except smtplib.SMTPAuthenticationError as e:
            raise MailError(f"SMTP login refused ({e.smtp_code})", permanent=True) from None
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"{type(e).__name__}: {e}") from None
        if refused:
            raise MailError(f"recipient refused: {list(refused)}", permanent=True)
        return msg["Message-ID"]


class DisabledMailer(Mailer):
    def send(self, mail: OutgoingEmail) -> str:
        raise MailError("e-mail is disabled (EMAIL_MODE=disabled)", permanent=True)


def build_mailer(settings: Settings) -> Mailer:
    return {"outbox": OutboxMailer, "smtp": SmtpMailer, "disabled": DisabledMailer}[settings.EMAIL_MODE](settings)
