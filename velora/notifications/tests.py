from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from business.models import Business
from notifications.management.commands.run_scheduler import summary_due, summary_window_start
from notifications.models import Notification

NAIROBI = ZoneInfo('Africa/Nairobi')


class SchedulerSummaryTests(TestCase):
    def test_window_starts_on_the_latest_monday_morning(self):
        # Wednesday 2026-12-02 10:00 -> Monday 2026-11-30 07:00.
        start = summary_window_start(datetime(2026, 12, 2, 10, tzinfo=NAIROBI))
        self.assertEqual(start, datetime(2026, 11, 30, 7, tzinfo=NAIROBI))

    def test_monday_before_seven_still_belongs_to_last_week(self):
        start = summary_window_start(datetime(2026, 11, 30, 6, 59, tzinfo=NAIROBI))
        self.assertEqual(start, datetime(2026, 11, 23, 7, tzinfo=NAIROBI))

    def test_due_until_a_summary_is_queued_this_week(self):
        owner = get_user_model().objects.create_user('owner@example.com', 'pass-12345', full_name='Owner')
        business = Business.objects.create(
            business_owner=owner, business_name='Glow Salon', business_description='Hair',
            whatsapp_number='+254700000000', business_email='glow@example.com',
        )
        now = timezone.now()
        self.assertTrue(summary_due(now))
        Notification.objects.create(
            business=business, event=Notification.Event.SUMMARY_REPORT, channel='email',
            to_address='glow@example.com', body='summary',
        )
        self.assertFalse(summary_due(now + timedelta(minutes=1)))
        # A week later the next one is due again.
        self.assertTrue(summary_due(now + timedelta(days=7, minutes=1)))
