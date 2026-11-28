"""WhatsApp adapter for the Meta WhatsApp Cloud API.

Settings: ``WHATSAPP_TOKEN`` (system user access token), ``WHATSAPP_PHONE_NUMBER_ID``,
``WHATSAPP_VERIFY_TOKEN`` (any string you choose, pasted into Meta too),
``WHATSAPP_APP_SECRET`` (App settings > Basic, signs every webhook) and
``WHATSAPP_DISPLAY_NUMBER`` (the number customers message, for wa.me links).

Free-form and button messages can only be sent within 24 hours of the
customer's last message. Outside that window Velora sends approved message
templates (see ``TEMPLATES`` and docs/BOTS.md).
"""

import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import engine, http
from .models import ChatIdentity, InboundUpdate

logger = logging.getLogger('velora.bots')
SESSION_WINDOW = timedelta(hours=24)


def configured():
    return bool(getattr(settings, 'WHATSAPP_TOKEN', '') and getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', ''))


def _endpoint():
    version = getattr(settings, 'WHATSAPP_API_VERSION', 'v23.0')
    return f'https://graph.facebook.com/{version}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages'


def send(payload):
    if not configured():
        raise http.ChatAPIError('WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID are not set.')
    body = {'messaging_product': 'whatsapp', 'recipient_type': 'individual', **payload}
    data = http.post_json(_endpoint(), body, headers={'Authorization': f'Bearer {settings.WHATSAPP_TOKEN}'})
    messages = data.get('messages') or [{}]
    return messages[0].get('id', '')


def wa_id(phone):
    """Meta's format for a number: digits only, with the country code."""
    return ''.join(c for c in str(phone) if c.isdigit())


# ---------------------------------------------------------------------------
# Signature and verification
# ---------------------------------------------------------------------------

def valid_signature(raw_body, header):
    secret = getattr(settings, 'WHATSAPP_APP_SECRET', '')
    if not secret or not header or not header.startswith('sha256='):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len('sha256='):])


def verify_challenge(params):
    """Answer Meta's GET verification: returns the challenge, or None."""
    token = getattr(settings, 'WHATSAPP_VERIFY_TOKEN', '')
    if (params.get('hub.mode') == 'subscribe' and token
            and hmac.compare_digest(params.get('hub.verify_token', ''), token)):
        return params.get('hub.challenge', '')
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _cut(text, size):
    text = str(text or '')
    return text if len(text) <= size else text[:size - 1].rstrip() + '…'


def message_payload(to, reply):
    text = reply.text
    if reply.request_contact:
        # Never needed on WhatsApp (the sender's number is known); kept for safety.
        return {'to': to, 'type': 'text', 'text': {'body': text}}
    if reply.link and not reply.options:
        return {'to': to, 'type': 'interactive', 'interactive': {
            'type': 'cta_url', 'body': {'text': _cut(text, 1024)},
            'action': {'name': 'cta_url', 'parameters': {'display_text': _cut(reply.link[0], 20),
                                                         'url': reply.link[1]}},
        }}
    if not reply.options:
        return {'to': to, 'type': 'text', 'text': {'body': _cut(text, 4096), 'preview_url': True}}
    if reply.link:
        text = f'{text}\n{reply.link[1]}'
    # Up to three short choices are buttons; services and bookings always use a list so
    # their price, duration or status (the description) shows.
    detailed = any(o.id.startswith(('svc:', 'bk:')) for o in reply.options)
    if len(reply.options) <= 3 and not detailed and all(len(o.title) <= 20 for o in reply.options):
        return {'to': to, 'type': 'interactive', 'interactive': {
            'type': 'button', 'body': {'text': _cut(text, 1024)},
            'action': {'buttons': [
                {'type': 'reply', 'reply': {'id': o.id[:256], 'title': o.title}} for o in reply.options
            ]},
        }}
    rows = []
    for o in reply.options[:10]:
        row = {'id': o.id[:200], 'title': _cut(o.title, 24)}
        description = o.description or (o.title[24:] if len(o.title) > 24 else '')
        if description:
            row['description'] = _cut(description, 72)
        rows.append(row)
    return {'to': to, 'type': 'interactive', 'interactive': {
        'type': 'list', 'body': {'text': _cut(text, 1024)},
        'action': {'button': 'Choose', 'sections': [{'title': 'Options', 'rows': rows}]},
    }}


def send_replies(to, replies):
    for reply in replies:
        send(message_payload(to, reply))


def in_session(phone):
    identity = ChatIdentity.objects.filter(channel='whatsapp', external_id=wa_id(phone)).first()
    return bool(identity and identity.last_inbound_at and timezone.now() - identity.last_inbound_at < SESSION_WINDOW)


# ---------------------------------------------------------------------------
# Webhook payloads
# ---------------------------------------------------------------------------

def parse_message(message, contacts):
    sender = message.get('from', '')
    name = ''
    for contact in contacts:
        if contact.get('wa_id') == sender:
            name = (contact.get('profile') or {}).get('name', '')
    incoming = engine.Incoming(channel='whatsapp', external_id=sender, name=name, phone=f'+{sender}')
    kind = message.get('type')
    if kind == 'text':
        incoming.text = (message.get('text') or {}).get('body', '')
    elif kind == 'interactive':
        interactive = message.get('interactive') or {}
        reply = interactive.get('button_reply') or interactive.get('list_reply') or {}
        incoming.action = reply.get('id', '')
    elif kind == 'button':  # quick-reply button on a template message
        button = message.get('button') or {}
        incoming.action = button.get('payload', '')
        incoming.text = '' if incoming.action else button.get('text', '')
    else:
        incoming.text = ''  # images, voice notes...: answer with the menu
    return incoming


def process_payload(payload):
    """Handle a webhook POST body. Returns the number of messages handled."""
    from notifications.models import Notification

    handled = 0
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value') or {}
            for status in value.get('statuses', []):
                _apply_status(Notification, status)
            contacts = value.get('contacts', [])
            for message in value.get('messages', []):
                message_id = message.get('id')
                if message_id:
                    _, created = InboundUpdate.objects.get_or_create(channel='whatsapp', external_id=message_id)
                    if not created:
                        continue
                incoming = parse_message(message, contacts)
                if not incoming.external_id:
                    continue
                replies = engine.handle(incoming)
                try:
                    send_replies(incoming.external_id, replies)
                except http.ChatAPIError as exc:
                    logger.warning('WhatsApp reply to %s failed: %s', incoming.external_id, exc)
                handled += 1
    return handled


def _apply_status(Notification, status):
    message_id = status.get('id')
    if not message_id:
        return
    n = Notification.objects.filter(channel='whatsapp', provider_message_id=message_id).first()
    if n is None:
        return
    state = status.get('status')
    if state in ('delivered', 'read') and n.status != Notification.Status.DELIVERED:
        n.status = Notification.Status.DELIVERED
        n.delivered_at = timezone.now()
        n.save(update_fields=['status', 'delivered_at'])
    elif state == 'failed':
        errors = status.get('errors') or [{}]
        n.status = Notification.Status.FAILED
        n.error = f'WhatsApp: {errors[0].get("title", "failed")} ({errors[0].get("code", "")})'[:2000]
        n.save(update_fields=['status', 'error'])
