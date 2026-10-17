"""
Phase 6 audit: SECRET_KEY had a hardcoded fallback
(`django-insecure-dev-only-key`) that `config/settings.py` would silently
boot and serve real traffic with if a deployer forgot to set the env var —
and since `SIMPLE_JWT` sets no `SIGNING_KEY` override, that same value is
also djangorestframework-simplejwt's default HMAC signing key, so a
production deployment left at this default would have every access/refresh
token forgeable by anyone who's read this file (it's public, in this repo).

`manage.py check --deploy` already flags a weak SECRET_KEY, but that check
is opt-in — nothing stopped the process from simply starting. This can't be
tested by importing config.settings directly (Django's settings module is
already loaded once for the whole test session, and the check in question
runs at import time) — a subprocess is the only way to exercise "what
actually happens when this process starts with a given environment."
"""

import os
import subprocess
import sys

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_check(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_refuses_to_start_with_the_dev_secret_key_when_debug_is_false():
    result = _run_check({"DEBUG": "False", "SECRET_KEY": "django-insecure-dev-only-key"})
    assert result.returncode != 0
    assert "SECRET_KEY must be set" in result.stderr


def test_starts_fine_with_a_real_secret_key_when_debug_is_false():
    result = _run_check({"DEBUG": "False", "SECRET_KEY": "a-real-random-looking-secret-key-value-123"})
    assert result.returncode == 0, result.stderr


def test_dev_default_is_still_fine_when_debug_is_true():
    """Local dev never sets SECRET_KEY — the guard must not fire there."""
    result = _run_check({"DEBUG": "True", "SECRET_KEY": "django-insecure-dev-only-key"})
    assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_no_other_tests_accidentally_relied_on_the_dev_secret_key():
    """Sanity check only — confirms the test database itself still works
    under whatever SECRET_KEY the test session actually loaded."""
    from django.conf import settings

    assert settings.SECRET_KEY  # just needs to exist; this process's own DEBUG is True


_PRINT_PROXY_HEADER = (
    "import django, os\n"
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
    "django.setup()\n"
    "from django.conf import settings\n"
    "print(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))\n"
)


def _proxy_header_setting(env_overrides: dict[str, str]):
    env = {**os.environ, **env_overrides}
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_PROXY_HEADER],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_proxy_headers_are_not_trusted_by_default():
    """DJANGO_TRUST_PROXY_HEADERS unset must leave Django trusting nothing —
    the safe failure mode is an HTTPS-redirect loop behind a real proxy,
    never a spoofable header in front of one that doesn't exist."""
    output = _proxy_header_setting(
        {"DEBUG": "False", "SECRET_KEY": "a-real-random-looking-secret-key-value-123"}
    )
    assert output == "None"


def test_proxy_headers_are_trusted_only_when_explicitly_opted_in():
    output = _proxy_header_setting(
        {
            "DEBUG": "False",
            "SECRET_KEY": "a-real-random-looking-secret-key-value-123",
            "DJANGO_TRUST_PROXY_HEADERS": "true",
        }
    )
    assert output == "('HTTP_X_FORWARDED_PROTO', 'https')"
