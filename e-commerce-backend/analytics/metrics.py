"""
Computes the platform-wide commerce-metrics snapshot that
`report_commerce_metrics` (management command, wrapped by
analytics/tasks.py for the daily Celery beat run) POSTs to gen-portal.

The returned shape is a FIXED CONTRACT, agreed with the engineer building
the receiving endpoint in gen-portal (a separate repo/service) in parallel
— do not rename, add, or drop a key without updating that contract on both
sides. See analytics/reporting.py for where it gets sent.

Every money and rate figure is computed as a `Decimal` and converted to
`str` only at the very end, never through a `float` — the same rule
payments/mpesa.py and orders/services.py follow for money, and it applies
just as much to a number that only ever leaves this process as a report.

Two keys below are genuinely `None` rather than a fabricated number —
Charter 04 section IV ("nothing untrue on a Genmars surface") applies to
this product too, even though it ships metrics about itself rather than to
a client directly:

  * `acquisition.businesses_churned_period` — `Business` has no
    status-change timestamp, only a generic `updated_at` that ANY field
    edit bumps. Reporting "churned in this period" off that column would
    silently count an unrelated edit (e.g. a contact email update) as
    churn, or miss a real status change made outside this period's
    `updated_at` window. There is nothing honest to compute here until a
    status-history table exists (mirroring orders.OrderStatusHistory).
  * `retention.paying_merchants_retained_mom` — there is no subscription or
    billing concept anywhere in this product; "paying merchants retained
    month over month" has no underlying data to measure at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from businesses.models import Business
from catalog.models import Product
from core.models import StoreSettings
from inventory.models import InventoryRecord
from orders.models import Order
from payments.models import Payment

from .models import StorefrontEvent

_MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
_ZERO_MONEY = Value(Decimal("0.00"), output_field=_MONEY_FIELD)


def _money_str(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _rate_str(numerator: int, denominator: int, places: str = "0.0001") -> str:
    """A ratio as a Decimal string, never a float division. `denominator=0`
    is not an error here — every call site below treats "nothing happened
    to divide by" as a rate of zero, not an undefined one, because a
    snapshot with no visits/add-to-carts/orders yet is a true zero, not a
    missing measurement."""
    if not denominator:
        return "0"
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal(places), rounding=ROUND_HALF_UP
        )
    )


def _sum_money(queryset, field: str) -> Decimal:
    return queryset.aggregate(total=Coalesce(Sum(field), _ZERO_MONEY, output_field=_MONEY_FIELD))["total"]


def compute_metrics(period_start: datetime, period_end: datetime) -> dict:
    """
    `period_start`/`period_end` scope every PERIOD figure below via the
    relevant model's own `created_at` (orders, payments) or `occurred_at`
    (storefront events) — i.e. "what happened in this window", not "what
    is true as of period_end". `inventory.*` and `theme_usage` are
    explicitly CURRENT-STATE snapshots instead (a stock level or a theme
    choice has no "within the period" reading that means anything), and
    `retention.repeat_orders_per_merchant` is all-time for the same reason
    "has this business ever placed more than one order" doesn't reset
    every 24 hours.
    """
    now = timezone.now()

    orders_in_period = Order.objects.filter(created_at__gte=period_start, created_at__lt=period_end)
    payments_in_period = Payment.objects.filter(created_at__gte=period_start, created_at__lt=period_end)
    events_in_period = StorefrontEvent.objects.filter(
        occurred_at__gte=period_start, occurred_at__lt=period_end
    )

    orders_placed = orders_in_period.count()
    orders_completed = orders_in_period.filter(
        status__in=[Order.Status.SHIPPED, Order.Status.DELIVERED]
    ).count()
    orders_cancelled = orders_in_period.filter(status=Order.Status.CANCELLED).count()
    orders_refunded = orders_in_period.filter(status=Order.Status.REFUNDED).count()
    failed_checkouts = orders_in_period.filter(status=Order.Status.PAYMENT_FAILED).count()

    total_order_value = _sum_money(orders_in_period, "total")
    outstanding_unpaid = _sum_money(
        orders_in_period.filter(
            status__in=[Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED]
        ),
        "total",
    )
    merchant_revenue_collected = _sum_money(
        payments_in_period.filter(status=Payment.Status.SUCCESSFUL), "amount"
    )
    failed_payment_attempts = payments_in_period.filter(status=Payment.Status.FAILED).count()

    average_order_value = (
        (total_order_value / Decimal(orders_placed)) if orders_placed else Decimal("0.00")
    )

    visits = events_in_period.filter(event_type=StorefrontEvent.EventType.VISIT).count()
    product_views = events_in_period.filter(event_type=StorefrontEvent.EventType.PRODUCT_VIEW).count()
    add_to_cart_events = events_in_period.filter(event_type=StorefrontEvent.EventType.ADD_TO_CART).count()
    checkout_starts = events_in_period.filter(event_type=StorefrontEvent.EventType.CHECKOUT_START).count()

    cart_abandonment_numerator = max(add_to_cart_events - orders_placed, 0)

    businesses_active_7d = (
        Business.objects.filter(orders__created_at__gte=now - timedelta(days=7)).distinct().count()
    )
    businesses_active_30d = (
        Business.objects.filter(orders__created_at__gte=now - timedelta(days=30)).distinct().count()
    )

    # "Has a published storefront" = active AND has sold (or could sell)
    # at least one published product. A business can be ACTIVE the moment
    # it's created, with an empty catalog — this definition is about
    # whether there is actually a storefront a customer could buy from
    # right now, not just whether the tenant row exists and isn't
    # suspended.
    businesses_active_with_storefront = (
        Business.objects.filter(
            status=Business.Status.ACTIVE, products__status=Product.Status.PUBLISHED
        )
        .distinct()
        .count()
    )

    products_out_of_stock = InventoryRecord.objects.filter(quantity__lte=0).count()
    orders_awaiting_fulfilment = Order.objects.filter(
        status__in=[Order.Status.PAID, Order.Status.PROCESSING]
    ).count()

    # StoreSettings.theme does not exist on this model yet — it lands in a
    # separate, unrelated change. Reporting must not fail or lie just
    # because that field isn't there yet: an empty dict here is an honest
    # "nothing to report", and this starts reporting real numbers the
    # moment that field exists, with no further change needed here.
    theme_choices = getattr(StoreSettings, "Theme", None)
    if theme_choices is not None:
        theme_usage = {
            value: StoreSettings.objects.filter(theme=value).count()
            for value, _label in theme_choices.choices
        }
    else:
        theme_usage = {}

    multi_order_businesses = list(
        Order.objects.values("business").annotate(order_count=Count("id")).filter(order_count__gt=1)
    )
    if multi_order_businesses:
        repeat_orders_per_merchant = str(
            (
                Decimal(sum(row["order_count"] for row in multi_order_businesses))
                / Decimal(len(multi_order_businesses))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    else:
        repeat_orders_per_merchant = "0"

    return {
        "acquisition": {
            "businesses_total": Business.objects.count(),
            "businesses_active_with_storefront": businesses_active_with_storefront,
            "businesses_onboarded_period": Business.objects.filter(
                created_at__gte=period_start, created_at__lt=period_end
            ).count(),
            "businesses_active_7d": businesses_active_7d,
            "businesses_active_30d": businesses_active_30d,
            # See the module docstring — Business has no status-change
            # timestamp to compute this from honestly.
            "businesses_churned_period": None,
        },
        "revenue": {
            "total_order_value_kes": _money_str(total_order_value),
            "orders_placed": orders_placed,
            "orders_completed": orders_completed,
            "orders_cancelled": orders_cancelled,
            "orders_refunded": orders_refunded,
            "merchant_revenue_collected_kes": _money_str(merchant_revenue_collected),
            "outstanding_unpaid_kes": _money_str(outstanding_unpaid),
            "average_order_value_kes": _money_str(average_order_value),
        },
        "storefront": {
            "visits": visits,
            "product_views": product_views,
            "add_to_cart_events": add_to_cart_events,
            "checkout_starts": checkout_starts,
            "orders_placed": orders_placed,
            "visitor_to_order_rate": _rate_str(orders_placed, visits),
            "cart_abandonment_rate": _rate_str(cart_abandonment_numerator, add_to_cart_events),
        },
        "platform_health": {
            "failed_checkouts": failed_checkouts,
            "failed_payment_attempts": failed_payment_attempts,
        },
        "inventory": {
            "products_out_of_stock": products_out_of_stock,
            "orders_awaiting_fulfilment": orders_awaiting_fulfilment,
            "cancellation_rate": _rate_str(orders_cancelled, orders_placed),
            "refund_rate": _rate_str(orders_refunded, orders_placed),
            "theme_usage": theme_usage,
        },
        "retention": {
            "merchants_active_7d": businesses_active_7d,
            "merchants_active_30d": businesses_active_30d,
            "repeat_orders_per_merchant": repeat_orders_per_merchant,
            # See the module docstring — nothing to compute this from.
            "paying_merchants_retained_mom": None,
        },
    }
