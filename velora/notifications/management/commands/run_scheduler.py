import signal
import time
from datetime import datetime, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from notifications.models import Notification

# Weekly summaries go out on Monday morning, Nairobi time.
SUMMARY_WEEKDAY = 0
SUMMARY_HOUR = 7


def summary_window_start(now):
    """The most recent Monday 07:00 (local time) at or before ``now``."""
    local = timezone.localtime(now)
    monday = (local - timedelta(days=(local.weekday() - SUMMARY_WEEKDAY) % 7)).date()
    start = timezone.make_aware(datetime(monday.year, monday.month, monday.day, SUMMARY_HOUR))
    if start > local:
        start -= timedelta(days=7)
    return start


def summary_due(now=None):
    """True when no summary report has been queued since the latest Monday 07:00.

    The answer comes from the notifications table rather than from memory, so a
    restart (or a laptop that was asleep on Monday) neither skips the week nor sends
    it twice.
    """
    now = now or timezone.now()
    start = summary_window_start(now)
    return not Notification.objects.filter(
        event=Notification.Event.SUMMARY_REPORT, created_at__gte=start,
    ).exists()


class Command(BaseCommand):
    help = ('Run the background jobs in one long-lived process: process_notifications every '
            'minute and send_summary_reports weekly. This is what the "scheduler" container runs.')

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=60, help='Seconds between runs (default 60)')

    def handle(self, *args, interval, **options):
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.stdout.write(f'Scheduler started, every {interval}s.')

        while not stopping:
            close_old_connections()
            # One failing run (a provider outage, the database restarting) must not stop
            # every later reminder, so errors are logged and the loop carries on.
            try:
                call_command('process_notifications', stdout=self.stdout)
                if summary_due():
                    call_command('send_summary_reports', days=7, stdout=self.stdout)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f'Scheduler run failed: {exc!r}')
            for _ in range(interval):
                if stopping:
                    break
                time.sleep(1)
        self.stdout.write('Scheduler stopped.')
