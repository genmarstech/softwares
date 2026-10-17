"""
Sends "I am running" to gen-portal (analytics/reporting.py::post_heartbeat).
Runs every five minutes off config/settings.py::CELERY_BEAT_SCHEDULE via
analytics/tasks.py; also runnable by hand to confirm GENMARS_SYSTEM_KEY works
without waiting for the next scheduled tick.

Same failure posture as report_commerce_metrics: GENMARS_SYSTEM_KEY may
legitimately be unset until a founder issues one, and this command fails
loudly (one clear log line, non-zero exit) but never in a way that crashes
the caller or that Celery's beat loop would treat as a reason to retry-storm
gen-portal.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from analytics.reporting import ReportingError, post_heartbeat


class Command(BaseCommand):
    help = "Sends a heartbeat to gen-portal so this system shows as watched."

    def handle(self, *args, **options):
        try:
            post_heartbeat()
        except ReportingError as exc:
            # exc's message is already safe to show and to log — see
            # reporting.py's own banner on why it never carries the key.
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS("Heartbeat sent to gen-portal."))
