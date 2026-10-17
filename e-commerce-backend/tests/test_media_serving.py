"""
Phase 7A deployment-readiness review: MEDIA_URL was never actually wired to
anything in config/urls.py — the comment on MEDIA_URL in settings.py claimed
uploads were "served by nginx/whitenoise in front in production", but neither
was configured anywhere, so every ProductImage.image URL the API returns has
been unreachable, dev and prod alike (confirmed gap, previously flagged in
commerce-platform's own CLAUDE.md). This is also why the demo-store runbook
this review writes depends on product images actually resolving.

Fixed in config/urls.py: always on in DEBUG, opt-in in production via
SERVE_MEDIA_VIA_DJANGO. The route is only added to urlpatterns once, at
import time, reading settings.DEBUG/settings.SERVE_MEDIA_VIA_DJANGO as they
stood when config.urls was first imported — same reason
test_deployment_safety.py uses a subprocess rather than reimporting
config.settings within one pytest session.
"""

import os
import subprocess
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK_SCRIPT = (
    "import django, os\n"
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
    "django.setup()\n"
    "from django.urls import get_resolver\n"
    "patterns = [str(p.pattern) for p in get_resolver().url_patterns]\n"
    "print('MEDIA_ROUTE_PRESENT' if any('media' in p for p in patterns) else 'MEDIA_ROUTE_ABSENT')\n"
)


def _media_route_present(env_overrides: dict[str, str]) -> bool:
    env = {**os.environ, **env_overrides}
    result = subprocess.run(
        [sys.executable, "-c", _CHECK_SCRIPT],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return "MEDIA_ROUTE_PRESENT" in result.stdout


def test_media_is_served_in_debug_regardless_of_the_flag():
    assert _media_route_present({"DEBUG": "True"})


def test_media_is_not_served_in_production_by_default():
    assert not _media_route_present(
        {"DEBUG": "False", "SECRET_KEY": "a-real-random-looking-secret-key-value-123"}
    )


def test_media_is_served_in_production_when_explicitly_opted_in():
    assert _media_route_present(
        {
            "DEBUG": "False",
            "SECRET_KEY": "a-real-random-looking-secret-key-value-123",
            "SERVE_MEDIA_VIA_DJANGO": "true",
        }
    )
