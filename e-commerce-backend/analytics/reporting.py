"""
POSTs the computed metrics snapshot (analytics/metrics.py) to gen-portal —
Genmars' own core Django backend, a SEPARATE service/repo, reached only over
the network — at `{GENMARS_API_ORIGIN}/api/systems/metrics`. Also carries
`post_heartbeat()`, a much smaller "I am running" POST to the same host's
`/api/systems/heartbeat` — see that function's own note on why it exists
separately from the metrics report.

── NO NEW DEPENDENCY ────────────────────────────────────────────────────────
Charter 03 section I, same reasoning as payments/mpesa.py: this is one JSON
POST carrying a bearer token. stdlib `urllib` has done this since Python 2;
nothing third-party belongs in the path a platform credential travels down.

── NEVER LOG GENMARS_SYSTEM_KEY ─────────────────────────────────────────────
Treated exactly like the M-Pesa consumer secret/passkey (root CLAUDE.md,
"Handling secrets"): never printed, never logged, never included in an
exception message that could reach a log line or a Celery failure trace.
Every error path below logs the HTTP status and gen-portal's own response
body, never the Authorization header or the key it carries.

── MISSING KEY IS EXPECTED, NOT A BUG ───────────────────────────────────────
`GENMARS_SYSTEM_KEY` is issued by a founder, by hand, through gen-portal's
ops UI — and only AFTER this ships. Until then, `post_metrics()` raises
`ReportingError` with a safe, specific message; it never lets an unset key
reach urllib as an empty/forged Authorization header, and the command and
Celery task that call this never let that turn into a crash or a retry
storm (see analytics/tasks.py and the management command's own banner).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal

from django.conf import settings

log = logging.getLogger(__name__)

TIMEOUT = 30
USER_AGENT = "genmars-commerce (+https://genmars.co.ke)"
METRICS_PATH = "/api/systems/metrics"
HEARTBEAT_PATH = "/api/systems/heartbeat"


class ReportingError(Exception):
    """Something kept the metrics snapshot or the heartbeat from reaching
    gen-portal. The message is safe to log and to print to a console — it
    never carries GENMARS_SYSTEM_KEY or any other credential."""


def _json_default(value):
    # Defensive only — analytics/metrics.py already converts every
    # Decimal to str before this module ever sees the dict. Kept so a
    # future field added to the metrics shape fails with a clear
    # TypeError here rather than silently round-tripping through float.
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def post_metrics(period_start: datetime, period_end: datetime, metrics: dict) -> None:
    api_key = getattr(settings, "GENMARS_SYSTEM_KEY", "") or ""
    if not api_key:
        raise ReportingError(
            "GENMARS_SYSTEM_KEY is not set — cannot report commerce metrics to "
            "gen-portal. A founder issues one from gen-portal's ops UI "
            "(Settings -> Engineering -> Systems); set it in this service's "
            "environment once issued."
        )

    origin = getattr(settings, "GENMARS_API_ORIGIN", "") or "https://api.genmars.co.ke"
    url = origin.rstrip("/") + METRICS_PATH

    body = json.dumps(
        {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "metrics": metrics,
        },
        default=_json_default,
    ).encode()

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if not (200 <= response.status < 300):
                text = response.read().decode(errors="replace")[:400]
                log.error("gen-portal metrics report -> unexpected status %s: %s", response.status, text)
                raise ReportingError(f"gen-portal returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")[:400]
        log.error("gen-portal metrics report -> %s %s", exc.code, text)
        raise ReportingError(f"gen-portal rejected the metrics report (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        log.error("gen-portal metrics report: could not reach %s (%s)", url, exc.reason)
        raise ReportingError("Could not reach gen-portal to report commerce metrics.") from exc


def post_heartbeat(version: str = "") -> None:
    """
    "I am running" — separate from post_metrics() on purpose.

    The daily metrics report is what makes /commerce show real numbers; this
    is what makes the Systems registry's "unwatched" count and heartbeat-
    staleness check mean something for this system, the same way every other
    registered system in gen-portal.System earns that by calling in. A
    system can be reporting metrics correctly and still read as unwatched
    if nothing ever calls this — they answer different questions.
    """
    api_key = getattr(settings, "GENMARS_SYSTEM_KEY", "") or ""
    if not api_key:
        raise ReportingError(
            "GENMARS_SYSTEM_KEY is not set — cannot send a heartbeat to "
            "gen-portal. A founder issues one from gen-portal's ops UI "
            "(Settings -> Engineering -> Systems); set it in this service's "
            "environment once issued."
        )

    origin = getattr(settings, "GENMARS_API_ORIGIN", "") or "https://api.genmars.co.ke"
    url = origin.rstrip("/") + HEARTBEAT_PATH

    payload = {"version": version} if version else {}
    body = json.dumps(payload).encode()

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if not (200 <= response.status < 300):
                text = response.read().decode(errors="replace")[:400]
                log.error("gen-portal heartbeat -> unexpected status %s: %s", response.status, text)
                raise ReportingError(f"gen-portal returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")[:400]
        log.error("gen-portal heartbeat -> %s %s", exc.code, text)
        raise ReportingError(f"gen-portal rejected the heartbeat (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        log.error("gen-portal heartbeat: could not reach %s (%s)", url, exc.reason)
        raise ReportingError("Could not reach gen-portal to send a heartbeat.") from exc
