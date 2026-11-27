"""Delivery providers.

- ``console``: logs the message and reports it as sent (development, and the
  fallback for every channel without a real provider).
- ``resend``: sends email through https://resend.com. Needs ``RESEND_API_KEY``
  and ``RESEND_FROM_EMAIL`` (a sender on a domain verified in Resend).

SMS, WhatsApp and push still use ``console``. Providers are picked per channel
in ``settings.VELORA_NOTIFICATION_PROVIDERS``.
"""

import json
import logging
import urllib.error
import urllib.request
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


class ResendProvider:
    name = 'resend'
    endpoint = 'https://api.resend.com/emails'
    timeout = 15

    def __init__(self):
        from django.conf import settings

        self.api_key = getattr(settings, 'RESEND_API_KEY', '')
        self.from_email = getattr(settings, 'RESEND_FROM_EMAIL', '')
        if not self.api_key or not self.from_email:
            raise ProviderError('Resend needs RESEND_API_KEY and RESEND_FROM_EMAIL.')

    def payload(self, notification):
        business = notification.business
        sender = self.from_email
        if '<' not in sender and business.business_name:
            # Show the salon's name in the inbox: "Glow Salon <bookings@getvelora.co.ke>"
            name = business.business_name.replace('"', '').replace('<', '').replace('>', '')
            sender = f'"{name}" <{sender}>'
        data = {
            'from': sender,
            'to': [notification.to_address],
            'subject': notification.subject or notification.get_event_display(),
            'text': notification.body,
        }
        # Customers who reply reach the business, not the Velora sender address.
        if notification.recipient_type == 'customer' and business.business_email:
            data['reply_to'] = business.business_email
        return data

    def send(self, notification):
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(self.payload(notification)).encode(),
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'User-Agent': 'velora-notifications',
                'Idempotency-Key': f'velora-notification-{notification.pk}',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:500]
            raise ProviderError(f'Resend returned {exc.code}: {detail}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f'Could not reach Resend: {exc}') from exc
        return body.get('id', '')


PROVIDERS = {
    'console': ConsoleProvider,
    'resend': ResendProvider,
}


def get_provider(channel):
    from django.conf import settings

    name = getattr(settings, 'VELORA_NOTIFICATION_PROVIDERS', {}).get(channel, 'console')
    provider_class = PROVIDERS.get(name)
    if provider_class is None:
        raise ProviderError(f'No provider called "{name}" is installed for {channel}.')
    return provider_class()
