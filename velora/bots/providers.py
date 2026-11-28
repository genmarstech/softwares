"""Notification providers for the bots, plugged into ``notifications.providers``.

Notifications (reminders, confirmations, "book again" nudges, new-booking
alerts) are written by ``notifications.services`` like every other channel.
These providers deliver them as chat messages with buttons that continue the
conversation: "My bookings", "Book again", or Confirm / Decline for the owner.
"""

import re

from django.conf import settings

from notifications.providers import ProviderError

from . import http, telegram, whatsapp
from .engine import Option, Reply

CUSTOMER_MANAGE_EVENTS = {'booking_created', 'booking_confirmed', 'booking_rescheduled', 'reminder',
                          'payment_receipt'}
CUSTOMER_REBOOK_EVENTS = {'follow_up', 'booking_cancelled', 'booking_no_show', 'hold_expired'}

DEFAULT_TEMPLATES = {
    'reminder': 'velora_appointment_reminder',
    'booking_confirmed': 'velora_booking_confirmed',
    'follow_up': 'velora_book_again',
    'staff_new_booking': 'velora_new_booking',
    'default': 'velora_booking_update',
}


def buttons_for(notification):
    """Buttons that go under a notification in chat."""
    booking = notification.booking
    business = notification.business
    if notification.recipient_type != 'customer':
        if (booking is not None and notification.event == 'staff_new_booking'
                and booking.status in ('pending', 'held')):
            return [Option(f'oc:{booking.id}', 'Confirm'), Option(f'od:{booking.id}', 'Decline')]
        return []
    ref = business.slug or business.id
    if notification.event in CUSTOMER_MANAGE_EVENTS:
        return [Option(f'mine:{ref}', 'My bookings')]
    if notification.event in CUSTOMER_REBOOK_EVENTS:
        return [Option(f'again:{ref}', 'Book again')]
    if notification.event == 'waitlist_opening':
        return [Option(f'new:{ref}', 'Book now')]
    return []


def as_reply(notification):
    return Reply(notification.body, options=buttons_for(notification))


class TelegramProvider:
    name = 'telegram'

    def __init__(self):
        if not telegram.configured():
            raise ProviderError('Telegram needs TELEGRAM_BOT_TOKEN.')

    def send(self, notification):
        try:
            result = telegram.call('sendMessage', telegram.message_payload(notification.to_address,
                                                                           as_reply(notification)))
        except http.ChatAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return str((result or {}).get('message_id', ''))


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

def _param(value, size=200):
    """Template parameters cannot hold new lines, tabs or runs of spaces."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return (text[:size - 1] + '…') if len(text) > size else (text or '-')


def _when(booking):
    from notifications.services import _when as when
    return when(booking)


def _service(booking):
    staff = f' with {booking.staff.staff_name}' if booking.staff else ''
    return f'{booking.service.service_name}{staff}'


def _message_only(notification):
    """The body without "Hi Jane, ", its links and sign-off, as one line."""
    first = notification.body.split('\n')[0]
    first = re.sub(r'^Hi [^,]*,\s*', '', first)
    return first[:1].upper() + first[1:]


def template_payload(notification):
    templates = {**DEFAULT_TEMPLATES, **getattr(settings, 'WHATSAPP_TEMPLATES', {})}
    language = getattr(settings, 'WHATSAPP_TEMPLATE_LANGUAGE', 'en')
    booking, business = notification.booking, notification.business
    first = (notification.recipient_name or 'there').split(' ')[0]
    event = notification.event
    buttons = []
    if event == 'reminder' and booking is not None:
        name = templates['reminder']
        params = [first, business.business_name, _service(booking), _when(booking)]
        buttons = [f'mine:{business.slug}']
    elif event == 'booking_confirmed' and booking is not None:
        name = templates['booking_confirmed']
        params = [first, _service(booking), business.business_name, _when(booking)]
    elif event == 'follow_up' and booking is not None:
        name = templates['follow_up']
        params = [first, business.business_name, booking.service.service_name]
        buttons = [f'again:{business.slug}']
    elif event == 'staff_new_booking' and booking is not None:
        name = templates['staff_new_booking']
        params = [business.business_name, booking.customer.full_name, _service(booking), _when(booking)]
        buttons = [f'oc:{booking.id}', f'od:{booking.id}']
    else:
        name = templates['default']
        params = [first, business.business_name, _message_only(notification)]
    components = [{'type': 'body', 'parameters': [{'type': 'text', 'text': _param(p)} for p in params]}]
    for index, payload in enumerate(buttons):
        components.append({'type': 'button', 'sub_type': 'quick_reply', 'index': str(index),
                           'parameters': [{'type': 'payload', 'payload': payload}]})
    return {'to': whatsapp.wa_id(notification.to_address), 'type': 'template',
            'template': {'name': name, 'language': {'code': language}, 'components': components}}


class WhatsAppCloudProvider:
    name = 'whatsapp_cloud'

    def __init__(self):
        if not whatsapp.configured():
            raise ProviderError('WhatsApp needs WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID.')

    def send(self, notification):
        to = whatsapp.wa_id(notification.to_address)
        if whatsapp.in_session(to):
            payload = whatsapp.message_payload(to, as_reply(notification))
        else:
            payload = template_payload(notification)
        try:
            return whatsapp.send(payload)
        except http.ChatAPIError as exc:
            raise ProviderError(f'WhatsApp: {exc}') from exc


PROVIDERS = {
    'telegram': TelegramProvider,
    'whatsapp_cloud': WhatsAppCloudProvider,
}
