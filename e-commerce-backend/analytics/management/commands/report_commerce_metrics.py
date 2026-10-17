"""
Computes a commerce-metrics snapshot (analytics/metrics.py) and reports it
to gen-portal (analytics/reporting.py). Runs daily off
config/settings.py::CELERY_BEAT_SCHEDULE via analytics/tasks.py; also
runnable by hand for a one-off or backfilled report.

── THE KEY MAY NOT EXIST YET ────────────────────────────────────────────────
GENMARS_SYSTEM_KEY is issued manually, after this ships, by a founder
through gen-portal's own ops UI. Until then this command fails LOUDLY (one
clear, specific log line and a non-zero exit) but SAFELY — it never raises
in a way that crashes the process that invoked it or that Celery's beat
loop would treat as a reason to retry-storm gen-portal.

── NEVER LOG THE KEY ────────────────────────────────────────────────────────
Same rule as payments/mpesa.py's consumer secret/passkey: GENMARS_SYSTEM_KEY
must never appear in a log line, an exception message, or a traceback. See
analytics/reporting.py, which is the only place that ever reads it.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import dateparse, timezone

from analytics.metrics import compute_metrics
from analytics.reporting import ReportingError, post_metrics


class Command(BaseCommand):
    help = "Computes a commerce-metrics snapshot and reports it to gen-portal."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period-hours",
            type=int,
            default=24,
            help="Length of the reporting window in hours, ending now. Default 24 (a daily snapshot).",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help=(
                "ISO8601 timestamp to use as period_start instead of --period-hours. "
                "period_end is always now."
            ),
        )

    def handle(self, *args, **options):
        period_end = timezone.now()

        if options["since"]:
            period_start = dateparse.parse_datetime(options["since"])
            if period_start is None:
                raise CommandError(f"--since {options['since']!r} is not a valid ISO8601 timestamp.")
            if timezone.is_naive(period_start):
                period_start = timezone.make_aware(period_start)
        else:
            period_start = period_end - timedelta(hours=options["period_hours"])

        if period_start >= period_end:
            raise CommandError("period_start must be before period_end.")

        metrics = compute_metrics(period_start, period_end)

        try:
            post_metrics(period_start, period_end, metrics)
        except ReportingError as exc:
            # exc's message is already safe to show and to log — see
            # reporting.py's own banner on why it never carries the key.
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"Reported commerce metrics for {period_start.isoformat()} .. {period_end.isoformat()}"
            )
        )
