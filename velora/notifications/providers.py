"""Delivery providers.

Only the development ``console`` provider exists for now: it logs the message
and reports it as sent. Real providers (an SMS gateway, the WhatsApp Business
API, an email service, push) are added here once the business picks them, and
selected per channel in ``settings.VELORA_NOTIFICATION_PROVIDERS``.
"""

import logging
import uuid

logger = logging.getLogger('velora.notifications')


class ProviderError(Exception):
    pass


class ConsoleProvider:
    name = 'console'

    def send(self, notification):
        logger.info(
            '[%s] to %s (%s): %s\n%s',
            notification.channel.upper(), notification.recipient_name or '-', notification.to_address,
            notification.subject or notification.get_event_display(), notification.body,
        )
        return f'console-{uuid.uuid4().hex[:12]}'


PROVIDERS = {
    'console': ConsoleProvider,
}


def get_provider(channel):
    from django.conf import settings

    name = getattr(settings, 'VELORA_NOTIFICATION_PROVIDERS', {}).get(channel, 'console')
    provider_class = PROVIDERS.get(name)
    if provider_class is None:
        raise ProviderError(f'No provider called "{name}" is installed for {channel}.')
    return provider_class()
