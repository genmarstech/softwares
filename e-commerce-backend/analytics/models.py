"""
Storefront-funnel instrumentation. `StorefrontEvent` is deliberately the
only model in this app, and deliberately thin: it counts things, it does
not watch people. No IP address, no user agent, no session id, no
anything that could re-identify a visitor — see `services.record()` for
the one write path onto this table and why a failure to write here must
never be allowed to break the request that triggered it.

This is a server-rendered/BFF architecture (the browser never calls this
Django API directly — see commerce-platform's own CLAUDE.md), so every
event below is logged from server-side code that already knows it happened,
never from client-side JS, and never needs any.
"""

from __future__ import annotations

from django.db import models

from businesses.models import Business
from catalog.models import Product


class StorefrontEvent(models.Model):
    class EventType(models.TextChoices):
        VISIT = "visit", "Visit"
        PRODUCT_VIEW = "product_view", "Product view"
        ADD_TO_CART = "add_to_cart", "Add to cart"
        CHECKOUT_START = "checkout_start", "Checkout start"

    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="storefront_events"
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    # Only meaningful for product_view/add_to_cart — a visit or a
    # checkout_start isn't about any one product. SET_NULL rather than
    # CASCADE: deleting a product must not quietly erase the aggregate
    # counts of what happened before it was deleted.
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="storefront_events",
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(
                fields=["business", "event_type", "occurred_at"],
                name="sfevent_biz_type_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} — {self.business.slug} @ {self.occurred_at:%Y-%m-%d %H:%M}"
