"""
Resend email backend.

Mirrors gen-portal's accounts/mail_backends.py deliberately, not
independently reinvented — same host, same constraint, same fix, and a
second implementation that drifted from the first would be a worse thing to
maintain than one shared piece of knowledge written twice.

Django's SMTP backend would also reach Resend — they publish an SMTP
endpoint — so this file has to justify itself. Two reasons, and the first is
decisive:

  1. **Outbound SMTP is not reliably available on the Hetzner host this
     project deploys to.** Hetzner blocks outbound 25 on every account and
     465/587 on new ones until support unblocks them. That failure is slow
     (a ten-second connect timeout per message) and looks nothing like "your
     mail provider is unreachable" in a log. HTTPS on 443 works from any
     container on that host, on the first deploy, with no ticket.

  2. **A failure says what failed.** Resend answers with a status code and a
     message, and a success carries an id that can be pasted into their
     dashboard to see what happened to that exact email.

── NO NEW DEPENDENCY ────────────────────────────────────────────────────────
Charter 03 §I (the same rule gen-portal's own copy of this file cites):
something enters the stack only when what is already in it genuinely cannot
do the job. Resend publish a `resend` package; this is one JSON POST with a
bearer token, which urllib has done since Python 2. requirements.txt is
unchanged.

── WHAT THIS MUST NEVER DO ──────────────────────────────────────────────────
**Never log the message body.** Low-stock alerts and order-status emails
don't carry a verification code the way gen-portal's do, but they do carry a
customer's name, phone number and delivery address — still not something to
put in a log line. Errors here log the status, Resend's own error text, and
the recipient — never the subject or body.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)

ENDPOINT = "https://api.resend.com/emails"

# ── THIS HEADER IS LOAD-BEARING. DO NOT REMOVE IT. ──────────────────────────
# Resend sits behind Cloudflare, which blocks urllib's default User-Agent
# outright (403 "error code: 1010" — a Cloudflare page, not a Resend error,
# so the body says nothing about mail). Confirmed in gen-portal's own
# production container; not re-verified independently here, since it is the
# same Resend endpoint and the same urllib client.
USER_AGENT = "genmars-commerce (+https://genmars.co.ke)"


class ResendBackend(BaseEmailBackend):
    """
    Send through Resend's HTTP API. Stateless — each message is one request,
    so a partial failure across several recipients is sane: one failing
    doesn't roll back the others.
    """

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        # Django 6.1 deprecates passing fail_silently through to the base
        # class (RemovedInDjango2028Warning) — "a subclass that supports
        # fail_silently must set its own attribute" is the warning's own
        # fix, so that's what happens here instead of super().__init__(
        # fail_silently=...). gen-portal's copy of this file predates this
        # Django version and still uses the old form; don't copy that back.
        super().__init__()
        self.fail_silently = fail_silently
        self.api_key: str = getattr(settings, "RESEND_API_KEY", "")
        self.timeout: int = getattr(settings, "EMAIL_TIMEOUT", 10)

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0

        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ValueError(
                "RESEND_API_KEY is empty. The Resend backend cannot send mail "
                "without it."
            )

        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    @staticmethod
    def _html_alternative(message) -> str | None:
        for alternative in getattr(message, "alternatives", None) or []:
            content = getattr(alternative, "content", None)
            mimetype = getattr(alternative, "mimetype", None)
            if content is None:
                try:
                    content, mimetype = alternative
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
            if mimetype == "text/html":
                return content
        return None

    def _send(self, message) -> bool:
        recipients = message.recipients()
        if not recipients:
            return False

        payload = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": recipients,
            "subject": message.subject,
            "text": message.body,
        }

        html = self._html_alternative(message)
        if html is not None:
            payload["html"] = html

        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
            log.info("resend accepted message id=%s", body.get("id"))
            return True

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            log.error("resend rejected message: HTTP %s %s", exc.code, detail)
            if not self.fail_silently:
                raise
            return False

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.error("resend unreachable: %s", exc)
            if not self.fail_silently:
                raise
            return False
