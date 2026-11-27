"""Booking-link slugs: /book/glow-salon instead of /book/12."""

import re

from django.utils.text import slugify

MAX_LENGTH = 80
MIN_LENGTH = 3

# Words that would clash with app routes or look official.
RESERVED = {
    'admin', 'api', 'app', 'book', 'booking', 'bookings', 'dashboard', 'help', 'login', 'logout',
    'manage', 'new', 'register', 'settings', 'support', 'velora', 'www',
}

SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def base_slug(name):
    slug = slugify(name or '')[:MAX_LENGTH].strip('-')
    if len(slug) < MIN_LENGTH or slug.isdigit() or slug in RESERVED:
        slug = f'{slug}-studio'.strip('-') if slug else 'business'
    return slug


def unique_business_slug(name, exclude_pk=None, model=None):
    """``glow-salon``, then ``glow-salon-2``, ``glow-salon-3``..."""
    if model is None:
        from .models import Business as model  # noqa: N813
    root = base_slug(name)
    candidate, n = root, 2
    taken = model.objects.all()
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    while taken.filter(slug=candidate).exists():
        suffix = f'-{n}'
        candidate = f'{root[:MAX_LENGTH - len(suffix)]}{suffix}'
        n += 1
    return candidate


def validate_slug_format(value):
    """Return an error message, or None if the slug is acceptable."""
    if not SLUG_RE.match(value):
        return 'Use lowercase letters, numbers and single hyphens, e.g. glow-salon.'
    if len(value) < MIN_LENGTH:
        return f'Use at least {MIN_LENGTH} characters.'
    if len(value) > MAX_LENGTH:
        return f'Use at most {MAX_LENGTH} characters.'
    if value.isdigit():
        return 'The link needs at least one letter.'
    if value in RESERVED:
        return 'This link is reserved. Please choose another.'
    return None
