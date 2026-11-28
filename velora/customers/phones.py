"""Phone numbers are a customer's identity across Velora.

They are stored in E.164 form (``+254712345678``) so the same person is found
whether they type ``0712 345 678``, ``254712345678`` or ``+254 712 345 678``,
and so a WhatsApp or Telegram bot can look a customer up by the number it sees.
"""

from django.conf import settings


def default_country_code():
    return str(getattr(settings, 'VELORA_DEFAULT_COUNTRY_CODE', '254')).lstrip('+')


def normalize_phone(value):
    """Return ``value`` as ``+<country><number>``, or '' if it is not a phone number."""
    raw = (value or '').strip()
    if not raw or not all(c.isdigit() or c in '+ -().' for c in raw):
        return ''
    digits = ''.join(c for c in raw if c.isdigit())
    if raw.startswith('+'):
        pass
    elif raw.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        # Local format: 0712 345 678 -> +254 712 345 678
        digits = default_country_code() + digits[1:]
    elif len(digits) <= 10:
        # Number typed without the leading 0 (712 345 678).
        digits = default_country_code() + digits
    if not 10 <= len(digits) <= 15:
        return ''
    return f'+{digits}'


def same_phone(a, b):
    na, nb = normalize_phone(a), normalize_phone(b)
    return bool(na) and na == nb
