import time

from django.core.management.base import BaseCommand

from bots import http, telegram


class Command(BaseCommand):
    help = ('Run the Telegram bot by polling, for a laptop without a public HTTPS URL. '
            'Removes any webhook first; run telegram_webhook again when you deploy.')

    def handle(self, *args, **options):
        telegram.call('deleteWebhook', {})
        me = telegram.call('getMe', {})
        self.stdout.write(f'Polling as @{me.get("username")}. Press Ctrl+C to stop.')
        offset = None
        while True:
            try:
                payload = {'timeout': 30, 'allowed_updates': ['message', 'callback_query']}
                if offset is not None:
                    payload['offset'] = offset
                for update in telegram.call('getUpdates', payload) or []:
                    offset = update['update_id'] + 1
                    try:
                        telegram.process_update(update)
                    except Exception as exc:  # noqa: BLE001 - keep polling
                        self.stderr.write(f'Update {update.get("update_id")} failed: {exc}')
            except http.ChatAPIError as exc:
                self.stderr.write(f'Telegram error: {exc}. Retrying in 5 seconds.')
                time.sleep(5)
            except KeyboardInterrupt:
                return
