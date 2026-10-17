"""
The one write path onto `StorefrontEvent`. Everything that logs a
storefront-funnel event — catalog's product list/detail views, cart's
add-item view, checkout initiation — calls `record()` and nothing else.

`record()` swallows its own errors on purpose. By the time any call site
reaches this, the real request (serving the catalog page, adding to the
cart, creating the order) has already succeeded — a failure writing one
analytics row is not a reason to turn that into a 500. Logging is the only
signal a lost event leaves behind.
"""

from __future__ import annotations

import logging

from django.db import transaction

from .models import StorefrontEvent

log = logging.getLogger(__name__)


def record(business, event_type: str, *, product=None) -> None:
    try:
        # A savepoint of its own, not just a try/except — a call site that
        # happens to run inside a broader atomic block (checkout's own
        # order-creation transaction, say, after a future refactor) must
        # not have ITS transaction poisoned by a failed analytics insert.
        # Postgres aborts the whole surrounding transaction on an
        # uncaught IntegrityError; this confines that to just this write.
        with transaction.atomic():
            StorefrontEvent.objects.create(
                business=business, event_type=event_type, product=product
            )
    except Exception:
        # Never propagate — see the module docstring. The business/product
        # repr here is safe to log: neither carries anything sensitive.
        log.exception(
            "Failed to record storefront event %r for business=%r product=%r",
            event_type, business, product,
        )
