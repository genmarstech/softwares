"""Telegram adapter: Bot API calls, update parsing and reply rendering.

Setup: create the bot with @BotFather, then set ``TELEGRAM_BOT_TOKEN``,
``TELEGRAM_BOT_USERNAME`` and ``TELEGRAM_WEBHOOK_SECRET``. Register the webhook
with ``python manage.py telegram_webhook --url https://<api-host>`` or, on a
laptop without a public URL, run ``python manage.py telegram_poll``.
"""

import logging

from django.conf import settings

from . import engine, http
from .models import InboundUpdate

logger = logging.getLogger('velora.bots')
API = 'https://api.telegram.org'


def configured():
    return bool(getattr(settings, 'TELEGRAM_BOT_TOKEN', ''))


def call(method, payload):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise http.ChatAPIError('TELEGRAM_BOT_TOKEN is not set.')
    data = http.post_json(f'{API}/bot{token}/{method}', payload, timeout=40)
    if not data.get('ok', False):
        raise http.ChatAPIError(f'Telegram {method} failed: {data.get("description", data)}')
    return data.get('result')


def reply_markup(reply):
    if reply.request_contact:
        return {
            'keyboard': [[{'text': 'Share my phone number', 'request_contact': True}]],
            'resize_keyboard': True, 'one_time_keyboard': True,
        }
    rows = []
    if reply.link:
        rows.append([{'text': reply.link[0], 'url': reply.link[1]}])
    for option in reply.options:
        title = option.title
        if option.description and not option.id.startswith(('slot:', 'day:')):
            title = f'{title} · {option.description}'
        rows.append([{'text': title[:64], 'callback_data': option.id[:64]}])
    if rows:
        return {'inline_keyboard': rows}
    if reply.remove_keyboard:
        return {'remove_keyboard': True}
    return None




def message_payload(chat_id, reply):
    payload = {'chat_id': chat_id, 'text': reply.text, 'disable_web_page_preview': True}
    markup = reply_markup(reply)
    if markup and 'inline_keyboard' in markup:
        markup['inline_keyboard'] = _pack_rows(markup['inline_keyboard'])
    if markup:
        payload['reply_markup'] = markup
    return payload


def _pack_rows(rows):
    """Put times (09:00) three to a row and days two to a row; everything else one per row."""
    packed, buffer, width = [], [], 0
    for row in rows:
        data = row[0].get('callback_data', '')
        size = 3 if data.startswith('slot:') else 2 if data.startswith('day:') else 1
        if size == 1:
            if buffer:
                packed.append(buffer)
                buffer = []
            packed.append(row)
            continue
        if buffer and width != size:
            packed.append(buffer)
            buffer = []
        width = size
        buffer.append(row[0])
        if len(buffer) == size:
            packed.append(buffer)
            buffer = []
    if buffer:
        packed.append(buffer)
    return packed


def send_replies(chat_id, replies):
    for reply in replies:
        call('sendMessage', message_payload(chat_id, reply))


def parse_update(update):
    """Turn a Telegram update into ``(Incoming, callback_query_id)``, or ``(None, None)``."""
    if 'callback_query' in update:
        cq = update['callback_query']
        message = cq.get('message') or {}
        chat = message.get('chat') or {}
        user = cq.get('from') or {}
        chat_id = chat.get('id', user.get('id'))
        if chat_id is None:
            return None, None
        return engine.Incoming(
            channel='telegram', external_id=str(chat_id), action=cq.get('data', ''), name=_name(user),
        ), cq.get('id')

    message = update.get('message')
    if not message:
        return None, None
    chat = message.get('chat') or {}
    if chat.get('type', 'private') != 'private':
        return None, None  # the bot only talks one to one
    user = message.get('from') or {}
    contact = message.get('contact') or {}
    incoming = engine.Incoming(
        channel='telegram', external_id=str(chat.get('id')), text=message.get('text', ''), name=_name(user),
    )
    if contact:
        incoming.contact_phone = contact.get('phone_number', '')
        # Telegram only attaches user_id when the contact is a Telegram user; the
        # share-contact button sends the person's own, verified number.
        incoming.contact_is_self = bool(user.get('id')) and contact.get('user_id') == user.get('id')
    return incoming, None


def _name(user):
    return ' '.join(p for p in (user.get('first_name', ''), user.get('last_name', '')) if p).strip()


def process_update(update):
    """Handle one update end to end. Safe to call twice with the same update."""
    update_id = update.get('update_id')
    if update_id is not None:
        _, created = InboundUpdate.objects.get_or_create(channel='telegram', external_id=str(update_id))
        if not created:
            return []
    incoming, callback_id = parse_update(update)
    if incoming is None:
        return []
    if callback_id:
        try:
            call('answerCallbackQuery', {'callback_query_id': callback_id})
        except http.ChatAPIError as exc:
            logger.warning('Telegram answerCallbackQuery failed: %s', exc)
    replies = engine.handle(incoming)
    try:
        send_replies(incoming.external_id, replies)
    except http.ChatAPIError as exc:
        logger.warning('Telegram reply to %s failed: %s', incoming.external_id, exc)
    return replies
