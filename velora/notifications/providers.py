"""Delivery providers.

- ``console``: logs the message and reports it as sent (development, and the
  fallback for every channel without a real provider).
- ``resend``: sends email through https://resend.com. Needs ``RESEND_API_KEY``
  and a sender on a domain verified in Resend: ``VELORA_EMAIL_FROM`` (falls back
  to ``RESEND_FROM_EMAIL``), shown as ``VELORA_EMAIL_FROM_NAME``. Each email has the
  plain text and a branded HTML version (see ``notifications/emails.py``).

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
        self.from_email = (getattr(settings, 'VELORA_EMAIL_FROM', '')
                           or getattr(settings, 'RESEND_FROM_EMAIL', ''))
        self.from_name = getattr(settings, 'VELORA_EMAIL_FROM_NAME', 'Velora') or 'Velora'
        if not self.api_key or not self.from_email:
            raise ProviderError('Resend needs RESEND_API_KEY and VELORA_EMAIL_FROM (or RESEND_FROM_EMAIL).')

    @staticmethod
    def _clean(name):
        return name.replace('"', '').replace('<', '').replace('>', '').strip()

    def sender(self, notification):
        if '<' in self.from_email:
            return self.from_email
        name = self._clean(self.from_name)
        business = notification.business
        if notification.recipient_type == 'customer' and business and business.business_name:
            # "Glow Salon via Velora <info@...>" so customers see who they booked with.
            name = f'{self._clean(business.business_name)} via {name}'
        return f'"{name}" <{self.from_email}>' if name else self.from_email

    def payload(self, notification):
        from .emails import render_html, text_footer

        business = notification.business
        data = {
            'from': self.sender(notification),
            'to': [notification.to_address],
            'subject': notification.subject or notification.get_event_display(),
            'text': f'{notification.body}\n\n{text_footer(notification)}',
            'html': render_html(notification),
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
