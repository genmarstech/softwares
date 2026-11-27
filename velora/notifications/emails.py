"""Branded HTML for notification emails.

Messages are written once as plain text (they also go out by SMS and WhatsApp). For email we
send that text as the plain-text part and wrap it in Velora's look for the HTML part:

- ``Label: https://...`` lines become buttons ("Manage your booking", "Open in Velora").
- ``Label: value`` lines (customer cards for the business) become a tidy details table.
- Other lines become paragraphs, with any links made clickable.
- A closing ``— Business name`` line becomes the sign-off.

Customer emails lead with the business's name; emails to the business lead with the Velora logo.
"""

import re

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import escape, urlize

LINK_LINE = re.compile(r'^(?P<label>[^:]{2,40}):\s*(?P<url>https?://\S+)$')
DETAIL_LINE = re.compile(r'^(?P<label>[A-Z][A-Za-z ]{1,24}):\s*(?P<value>.+)$')


def logo_url():
    return getattr(settings, 'VELORA_EMAIL_LOGO_URL', '') or f'{settings.FRONTEND_URL}/brand/email-logo.png'


def _blocks(body):
    """Turn the plain text into a list of (kind, data) blocks for the template."""
    blocks, details, buttons, signoff = [], [], [], ''
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('— ') or line.startswith('-- '):
            signoff = line.lstrip('—- ').strip()
            continue
        link = LINK_LINE.match(line)
        if link:
            buttons.append({'label': link['label'].strip(), 'url': link['url']})
            continue
        detail = DETAIL_LINE.match(line)
        if detail:
            details.append({'label': detail['label'], 'value': detail['value']})
            continue
        if details:
            blocks.append(('details', details))
            details = []
        blocks.append(('text', urlize(escape(line), nofollow=True, autoescape=False)))
    if details:
        blocks.append(('details', details))
    return blocks, buttons, signoff


def render_html(notification):
    business = notification.business
    blocks, buttons, signoff = _blocks(notification.body or '')
    for_customer = notification.recipient_type == 'customer'
    subject = notification.subject or notification.get_event_display()
    name = business.business_name if business else ''
    # The business name already heads customer emails, so drop it from the heading.
    heading = subject.removesuffix(f' · {name}') if for_customer and name else subject
    return render_to_string('notifications/email.html', {
        'subject': subject,
        'heading': heading,
        'can_reply': bool(for_customer and business and business.business_email),
        'blocks': blocks,
        'buttons': buttons,
        'signoff': signoff,
        'for_customer': for_customer,
        'business_name': business.business_name if business else '',
        'logo_url': logo_url(),
        'site_url': settings.FRONTEND_URL,
    })
