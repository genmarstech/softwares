from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bots import http, telegram


class Command(BaseCommand):
    help = 'Point the Telegram bot at this server: telegram_webhook --url https://api.example.com'

    def add_arguments(self, parser):
        parser.add_argument('--url', help='Public HTTPS base URL of this backend, e.g. https://api.getvelora.co.ke')
        parser.add_argument('--delete', action='store_true', help='Remove the webhook (for telegram_poll)')

    def handle(self, *args, **options):
        try:
            if options['delete']:
                telegram.call('deleteWebhook', {})
                self.stdout.write('Webhook removed.')
                return
            if not options['url']:
                raise CommandError('Pass --url https://<your-api-host>')
            if not settings.TELEGRAM_WEBHOOK_SECRET:
                raise CommandError('Set TELEGRAM_WEBHOOK_SECRET first.')
            url = options['url'].rstrip('/') + '/bots/telegram/webhook/'
            telegram.call('setWebhook', {
                'url': url, 'secret_token': settings.TELEGRAM_WEBHOOK_SECRET,
                'allowed_updates': ['message', 'callback_query'], 'drop_pending_updates': False,
            })
            telegram.call('setMyCommands', {'commands': [
                {'command': 'menu', 'description': 'Book, rebook or see your bookings'},
                {'command': 'stop', 'description': 'Stop reminders and updates'},
            ]})
            info = telegram.call('getWebhookInfo', {})
        except http.ChatAPIError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f'Webhook set to {info.get("url")}. Pending updates: {info.get("pending_update_count", 0)}.')
