from django.core.management.base import BaseCommand

from bookings.services import expire_holds
from notifications.services import dispatch_due


class Command(BaseCommand):
    help = 'Expire unpaid booking holds and send every notification that is due. Run it every minute.'

    def handle(self, *args, **options):
        expired = expire_holds()
        sent = dispatch_due(limit=1000)
        self.stdout.write(f'Expired holds: {expired}. Notifications attempted: {sent}.')
