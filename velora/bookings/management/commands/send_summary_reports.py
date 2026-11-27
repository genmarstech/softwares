from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.analytics import build_report
from bookings.services import business_tz, get_policy
from business.models import Business
from notifications.services import dispatch_due, queue


class Command(BaseCommand):
    help = 'Email each business owner a summary of the last N days (default 7). Schedule it weekly.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)

    def handle(self, *args, days, **options):
        count = 0
        for business in Business.objects.select_related('business_owner'):
            tz = business_tz(get_policy(business))
            date_to = timezone.now().astimezone(tz).date() - timedelta(days=1)
            date_from = date_to - timedelta(days=days - 1)
            r = build_report(business, date_from, date_to)
            body = (
                f'{business.business_name}: {date_from:%d %b} to {date_to:%d %b}\n'
                f'Bookings: {r["bookings"]["total"]} '
                f'(completed {r["bookings"]["by_status"]["completed"]}, '
                f'cancelled {r["bookings"]["by_status"]["cancelled"]}, '
                f'no-shows {r["bookings"]["by_status"]["no_show"]})\n'
                f'No-show rate: {r["attendance"]["no_show_rate_pct"]}%\n'
                f'Occupancy: {r["capacity"]["occupancy_pct"]}%\n'
                f'Revenue collected: {r["revenue"]["collected"]}\n'
                f'New customers: {r["customers"]["new"]}, returning: {r["customers"]["returning"]}'
            )
            email = business.business_email or business.business_owner.email
            queue(business=business, event='summary_report', channel='email', to_address=email,
                  subject=f'Your Velora summary · {business.business_name}', body=body,
                  recipient_type='business', recipient_name=business.business_owner.full_name)
            count += 1
        dispatch_due()
        self.stdout.write(f'Queued {count} summary report(s).')
