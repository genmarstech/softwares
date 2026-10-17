"""
The Resend backend (accounts/mail_backends.py).

Ported from gen-portal's own test_mail_resend.py, not independently
reinvented — same backend, same guarantees to defend. Every test here uses a
fake transport; nothing in this file may reach the network, and the rule
these tests exist to defend is the one in mail_backends.py: a customer's
name, phone number and delivery address must never end up in a log line.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest
from django.core.mail import EmailMessage, send_mail
from django.test import override_settings

from accounts.mail_backends import ResendBackend

BACKEND = "accounts.mail_backends.ResendBackend"


class FakeResponse(io.BytesIO):
    """Enough of an HTTPResponse for a context manager and a read()."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def capture(monkeypatch):
    """Swap urlopen for a recorder. Returns the list of requests it saw."""
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "body": json.loads(request.data.decode()),
                "timeout": timeout,
            }
        )
        return FakeResponse(b'{"id": "re_123"}')

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", fake_urlopen)
    return seen


@override_settings(RESEND_API_KEY="re_test_key", DEFAULT_FROM_EMAIL="store@genmars.co.ke")
def test_sends_one_request_per_message(capture):
    backend = ResendBackend()
    sent = backend.send_messages(
        [
            EmailMessage("Your order has shipped", "Order GM-1 is on its way", to=["a@b.co.ke"]),
            EmailMessage("Low stock: Demo Mouse", "3 units remaining", to=["owner@b.co.ke"]),
        ]
    )

    assert sent == 2
    assert len(capture) == 2
    assert capture[0]["url"] == "https://api.resend.com/emails"
    assert capture[0]["method"] == "POST"


@override_settings(RESEND_API_KEY="re_test_key", DEFAULT_FROM_EMAIL="store@genmars.co.ke")
def test_builds_the_payload_resend_expects(capture):
    ResendBackend().send_messages(
        [EmailMessage("Subject", "Body", "store@genmars.co.ke", ["client@example.co.ke"])]
    )

    body = capture[0]["body"]
    assert body == {
        "from": "store@genmars.co.ke",
        "to": ["client@example.co.ke"],
        "subject": "Subject",
        "text": "Body",
    }
    assert capture[0]["headers"]["authorization"] == "Bearer re_test_key"
    assert capture[0]["headers"]["content-type"] == "application/json"


@override_settings(RESEND_API_KEY="re_test_key")
def test_a_user_agent_is_always_sent(capture):
    """
    Resend is behind Cloudflare, which blocks urllib's default User-Agent
    with a 403 "error code: 1010" — a Cloudflare page, not a Resend error.
    Confirmed in gen-portal's own production container against this same
    endpoint; carried forward here as a regression guard, not re-verified
    independently.
    """
    ResendBackend().send_messages([EmailMessage("S", "B", "store@genmars.co.ke", ["a@b.co.ke"])])
    ua = capture[0]["headers"]["user-agent"]
    assert ua and "urllib" not in ua.lower()


@override_settings(RESEND_API_KEY="re_test_key")
def test_every_recipient_is_addressed(capture):
    """cc and bcc are recipients too. Dropping them loses mail silently."""
    ResendBackend().send_messages(
        [
            EmailMessage(
                "S", "B", "store@genmars.co.ke",
                to=["one@x.co.ke"], cc=["two@x.co.ke"], bcc=["three@x.co.ke"],
            )
        ]
    )
    assert set(capture[0]["body"]["to"]) == {"one@x.co.ke", "two@x.co.ke", "three@x.co.ke"}


@override_settings(RESEND_API_KEY="")
def test_a_missing_key_is_loud(capture):
    """
    A missing key must not be swallowed — silently returning 0 here would
    mean checkout succeeds and the owner's order notification (or a
    customer's) is simply never sent, with nothing in any log to explain why.
    """
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        ResendBackend().send_messages([EmailMessage("S", "B", "store@genmars.co.ke", ["a@b.co.ke"])])
    assert capture == []


@override_settings(RESEND_API_KEY="")
def test_a_missing_key_is_silent_only_when_asked(capture):
    assert ResendBackend(fail_silently=True).send_messages(
        [EmailMessage("S", "B", "store@genmars.co.ke", ["a@b.co.ke"])]
    ) == 0


@override_settings(RESEND_API_KEY="re_test_key")
def test_no_messages_makes_no_requests(capture):
    assert ResendBackend().send_messages([]) == 0
    assert capture == []


@override_settings(RESEND_API_KEY="re_test_key")
def test_an_http_error_raises_and_does_not_pretend(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.resend.com/emails", 403, "Forbidden", {},
            io.BytesIO(b'{"message": "The genmars.co.ke domain is not verified."}'),
        )

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)
    with pytest.raises(urllib.error.HTTPError):
        ResendBackend().send_messages([EmailMessage("S", "B", "store@genmars.co.ke", ["a@b.co.ke"])])


@override_settings(RESEND_API_KEY="re_test_key")
def test_a_network_error_raises(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        ResendBackend().send_messages([EmailMessage("S", "B", "store@genmars.co.ke", ["a@b.co.ke"])])


@override_settings(RESEND_API_KEY="re_secret_key_value")
def test_nothing_secret_reaches_the_logs(monkeypatch, caplog):
    """
    THE test in this file. A customer's delivery phone number and address,
    and the API key, must never appear in a log line — not on success, and
    not on the error path, which is exactly where someone reaches for "just
    log the payload" while debugging.
    """
    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.resend.com/emails", 422, "Unprocessable", {},
            io.BytesIO(b'{"message": "Invalid to field."}'),
        )

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)

    with caplog.at_level(logging.DEBUG):
        ResendBackend(fail_silently=True).send_messages(
            [EmailMessage("Your order has shipped", "Call 0712345678 to arrange delivery",
                          "store@genmars.co.ke", ["a@b.co.ke"])]
        )

    logged = caplog.text
    assert "0712345678" not in logged
    assert "re_secret_key_value" not in logged
    assert "arrange delivery" not in logged
    assert "422" in logged


@override_settings(RESEND_API_KEY="re_test_key")
def test_success_logs_the_id_and_nothing_else(capture, caplog):
    with caplog.at_level(logging.DEBUG):
        ResendBackend().send_messages(
            [EmailMessage("Your order has shipped", "Call 0712345678 to arrange delivery",
                          "store@genmars.co.ke", ["a@b.co.ke"])]
        )
    assert "re_123" in caplog.text
    assert "0712345678" not in caplog.text


@override_settings(
    MAILERS={"default": {"BACKEND": BACKEND, "OPTIONS": {}}},
    RESEND_API_KEY="re_test_key",
    DEFAULT_FROM_EMAIL="store@genmars.co.ke",
)
def test_django_send_mail_routes_through_it(capture):
    """
    The integration that matters: accounts/views.py's PasswordResetRequestView
    (and delivery/tasks.py) call django.core.mail.send_mail, not this class
    directly — and with MAILERS configured (confirmed via
    django.core.mail.get_connection's own source: `mailers._is_configured`
    takes priority and ignores settings.EMAIL_BACKEND entirely), it's
    MAILERS["default"]["BACKEND"] that has to point here, not EMAIL_BACKEND.
    If that wiring is wrong, every test above passes and no client ever gets
    an email.
    """
    send_mail(
        subject="Your password reset code",
        message="Your code is 135790. It expires in 15 minutes.",
        from_email=None,
        recipient_list=["client@example.co.ke"],
    )

    assert len(capture) == 1
    assert capture[0]["body"]["to"] == ["client@example.co.ke"]
    assert capture[0]["body"]["subject"] == "Your password reset code"
    assert "135790" in capture[0]["body"]["text"]
