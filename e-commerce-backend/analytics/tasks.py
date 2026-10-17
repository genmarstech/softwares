"""
The beat tasks — see config/settings.py::CELERY_BEAT_SCHEDULE for the
schedules and analytics/management/commands/ for what each one actually
runs. Both wrappers exist so a failure (most expectedly: no
GENMARS_SYSTEM_KEY yet) is a logged, failed Celery task and nothing more —
never an exception that escapes into the worker loop, and never something
Celery's own retry machinery would hammer gen-portal with.
"""

import logging

from celery import shared_task
from django.core.management import call_command

log = logging.getLogger(__name__)


@shared_task
def report_commerce_metrics() -> None:
    try:
        call_command("report_commerce_metrics")
    except Exception:
        # The command itself already logged a specific, safe message (see
        # its own banner on why GENMARS_SYSTEM_KEY may legitimately be
        # unset); this is the backstop that keeps that failure from ever
        # reaching Celery as an unhandled task error.
        log.exception("report_commerce_metrics task failed")


@shared_task
def report_commerce_heartbeat() -> None:
    try:
        call_command("report_commerce_heartbeat")
    except Exception:
        # Same backstop as report_commerce_metrics above — the command
        # already logged a specific, safe message.
        log.exception("report_commerce_heartbeat task failed")
