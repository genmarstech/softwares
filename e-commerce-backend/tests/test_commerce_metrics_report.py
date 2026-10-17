"""
Covers analytics/metrics.py (computation correctness against known
fixtures, including the zero-orders/zero-visits edge cases) and the
report_commerce_metrics management command (the POST's auth header and
body shape — mocked, never a real network call — and that a missing
GENMARS_SYSTEM_KEY fails cleanly without raising past the command).
"""

import io
import urllib.error
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from analytics.metrics import compute_metrics
from analytics.models import StorefrontEvent
from analytics.reporting import ReportingError, post_metrics
from catalog.models import Product
from core.models import StoreSettings
from inventory.models import InventoryRecord
from orders.models import Order
from payments.models import Payment


def _make_order(business, customer, *, status=Order.Status.AWAITING_PAYMENT, total="1000.00"):
    return Order.objects.create(
        business=business,
        customer=customer,
        status=status,
        subtotal=total,
        total=total,
        contact_email=customer.email,
        contact_phone="254712345678",
        delivery_recipient_name="Jane Doe",
        delivery_line1="123 Example Rd",
        delivery_city="Nairobi",
        delivery_phone="254712345678",
    )


@pytest.mark.django_db
class TestComputeMetricsZeroState:
    def test_an_empty_platform_divides_by_zero_safely(self, business):
        period_end = timezone.now()
        period_start = period_end - timedelta(hours=24)

        metrics = compute_metrics(period_start, period_end)

        assert metrics["revenue"]["orders_placed"] == 0
        assert metrics["revenue"]["total_order_value_kes"] == "0.00"
        assert metrics["revenue"]["average_order_value_kes"] == "0.00"
        assert metrics["storefront"]["visits"] == 0
        assert metrics["storefront"]["visitor_to_order_rate"] == "0"
        assert metrics["storefront"]["cart_abandonment_rate"] == "0"
        assert metrics["inventory"]["cancellation_rate"] == "0"
        assert metrics["inventory"]["refund_rate"] == "0"
        assert metrics["retention"]["repeat_orders_per_merchant"] == "0"
        # Never fabricated.
        assert metrics["acquisition"]["businesses_churned_period"] is None
        assert metrics["retention"]["paying_merchants_retained_mom"] is None


@pytest.mark.django_db
class TestComputeMetricsKnownFixtures:
    def test_revenue_and_storefront_figures_match_known_rows(self, business, customer):
        period_start = timezone.now() - timedelta(hours=24)

        paid = _make_order(business, customer, status=Order.Status.PAID, total="500.00")
        shipped = _make_order(business, customer, status=Order.Status.SHIPPED, total="700.00")
        cancelled = _make_order(business, customer, status=Order.Status.CANCELLED, total="300.00")
        awaiting = _make_order(business, customer, status=Order.Status.AWAITING_PAYMENT, total="200.00")
        failed = _make_order(business, customer, status=Order.Status.PAYMENT_FAILED, total="100.00")

        Payment.objects.create(order=paid, amount="500.00", status=Payment.Status.SUCCESSFUL)
        Payment.objects.create(order=shipped, amount="700.00", status=Payment.Status.SUCCESSFUL)
        Payment.objects.create(order=awaiting, amount="200.00", status=Payment.Status.FAILED)

        for _ in range(10):
            StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.VISIT)
        for _ in range(4):
            StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.ADD_TO_CART)
        StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.CHECKOUT_START)

        period_end = timezone.now()
        metrics = compute_metrics(period_start, period_end)
        revenue = metrics["revenue"]
        storefront = metrics["storefront"]

        assert revenue["orders_placed"] == 5
        assert revenue["orders_completed"] == 1  # shipped only (delivered/shipped)
        assert revenue["orders_cancelled"] == 1
        assert revenue["orders_refunded"] == 0
        assert revenue["total_order_value_kes"] == str(
            Decimal("500.00") + Decimal("700.00") + Decimal("300.00") + Decimal("200.00") + Decimal("100.00")
        )
        assert revenue["merchant_revenue_collected_kes"] == "1200.00"
        assert revenue["outstanding_unpaid_kes"] == "300.00"  # awaiting (200) + failed (100)
        assert revenue["average_order_value_kes"] == str(
            (Decimal("1800.00") / Decimal(5)).quantize(Decimal("0.01"))
        )

        assert storefront["visits"] == 10
        assert storefront["add_to_cart_events"] == 4
        assert storefront["checkout_starts"] == 1
        assert storefront["orders_placed"] == 5
        # 5 orders / 10 visits = 0.5
        assert storefront["visitor_to_order_rate"] == "0.5000"
        # (4 add-to-carts - 5 orders) is negative -> clamp to 0 -> rate 0.0000
        assert storefront["cart_abandonment_rate"] == "0.0000"

        assert metrics["platform_health"]["failed_checkouts"] == 1
        assert metrics["platform_health"]["failed_payment_attempts"] == 1

    def test_cart_abandonment_rate_when_carts_outnumber_orders(self, business, customer):
        period_start = timezone.now() - timedelta(hours=24)

        _make_order(business, customer, status=Order.Status.PAID, total="100.00")
        for _ in range(4):
            StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.ADD_TO_CART)

        period_end = timezone.now()
        metrics = compute_metrics(period_start, period_end)

        # (4 add-to-carts - 1 order) / 4 add-to-carts = 0.75
        assert metrics["storefront"]["cart_abandonment_rate"] == "0.7500"

    def test_cart_abandonment_rate_clamps_to_zero_when_orders_outnumber_carts(self, business, customer):
        """More orders than add-to-cart events (e.g. a staff-created order
        with no storefront cart behind it) must not produce a negative
        rate — see analytics/metrics.py's own clamp."""
        period_start = timezone.now() - timedelta(hours=24)

        for _ in range(5):
            _make_order(business, customer, status=Order.Status.PAID, total="100.00")
        for _ in range(4):
            StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.ADD_TO_CART)

        period_end = timezone.now()
        metrics = compute_metrics(period_start, period_end)

        assert metrics["storefront"]["cart_abandonment_rate"] == "0.0000"

    def test_events_and_orders_outside_the_period_are_excluded(self, business, customer):
        period_end = timezone.now() - timedelta(days=2)
        period_start = period_end - timedelta(hours=24)

        # Created "now" — outside the (deliberately past) period above.
        _make_order(business, customer, status=Order.Status.PAID, total="900.00")
        StorefrontEvent.objects.create(business=business, event_type=StorefrontEvent.EventType.VISIT)

        metrics = compute_metrics(period_start, period_end)

        assert metrics["revenue"]["orders_placed"] == 0
        assert metrics["revenue"]["total_order_value_kes"] == "0.00"
        assert metrics["storefront"]["visits"] == 0

    def test_inventory_snapshot_is_current_not_period_scoped(self, business, second_business):
        out_of_stock_product = Product.objects.create(
            name="Out", sku="OUT-1", price="100.00", status=Product.Status.PUBLISHED, business=business
        )
        InventoryRecord.objects.create(product=out_of_stock_product, quantity=0, business=business)

        in_stock_product = Product.objects.create(
            name="In", sku="IN-1", price="100.00", status=Product.Status.PUBLISHED, business=business
        )
        InventoryRecord.objects.create(product=in_stock_product, quantity=5, business=business)

        period_end = timezone.now()
        period_start = period_end - timedelta(hours=24)
        metrics = compute_metrics(period_start, period_end)

        assert metrics["inventory"]["products_out_of_stock"] >= 1

    def test_theme_usage_counts_stores_per_theme_now_the_field_exists(self, business, second_business):
        # The getattr(StoreSettings, "Theme", None) fallback in
        # analytics/metrics.py was written defensively, for a repo state
        # where this field didn't exist yet — see its own comment at the
        # theme_usage block. Now it does: this proves the real branch
        # computes correctly rather than just not crashing.
        assert hasattr(StoreSettings, "Theme")

        for shop in (business, second_business):
            settings = StoreSettings.load(shop)
            settings.theme = StoreSettings.Theme.FASHION
            settings.save()

        period_end = timezone.now()
        period_start = period_end - timedelta(hours=24)
        metrics = compute_metrics(period_start, period_end)

        assert metrics["inventory"]["theme_usage"]["fashion"] == 2
        assert metrics["inventory"]["theme_usage"]["default"] == 0

    def test_businesses_active_with_storefront_requires_active_status_and_a_published_product(
        self, business, second_business, make_user
    ):
        Product.objects.create(
            name="Live", sku="LIVE-1", price="50.00", status=Product.Status.PUBLISHED, business=business
        )
        # second_business has no published product at all.

        period_end = timezone.now()
        period_start = period_end - timedelta(hours=24)
        metrics = compute_metrics(period_start, period_end)

        assert metrics["acquisition"]["businesses_active_with_storefront"] == 1

    def test_repeat_orders_per_merchant_only_counts_businesses_with_more_than_one_order(
        self, business, second_business, customer, second_business_owner
    ):
        _make_order(business, customer, status=Order.Status.PAID, total="100.00")
        _make_order(business, customer, status=Order.Status.PAID, total="100.00")
        _make_order(business, customer, status=Order.Status.PAID, total="100.00")
        _make_order(second_business, second_business_owner, status=Order.Status.PAID, total="50.00")

        period_end = timezone.now()
        period_start = period_end - timedelta(hours=24)
        metrics = compute_metrics(period_start, period_end)

        # Only `business` qualifies (3 orders, > 1); second_business has
        # exactly one and is excluded from the average entirely.
        assert metrics["retention"]["repeat_orders_per_merchant"] == "3.00"


@pytest.mark.django_db
class TestReportingErrors:
    def test_missing_system_key_raises_a_safe_reporting_error(self, business, settings):
        settings.GENMARS_SYSTEM_KEY = ""

        with pytest.raises(ReportingError, match="GENMARS_SYSTEM_KEY"):
            post_metrics(timezone.now() - timedelta(hours=24), timezone.now(), {})

    def test_the_command_fails_cleanly_without_a_system_key(self, business, settings):
        settings.GENMARS_SYSTEM_KEY = ""

        with pytest.raises(CommandError):
            call_command("report_commerce_metrics")

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_posts_the_right_auth_header_and_body_shape(self, mock_urlopen, business, settings):
        settings.GENMARS_SYSTEM_KEY = "test-system-key"
        settings.GENMARS_API_ORIGIN = "https://api.genmars.test"
        mock_urlopen.return_value.__enter__.return_value.status = 200

        call_command("report_commerce_metrics", "--period-hours", "24")

        assert mock_urlopen.called
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://api.genmars.test/api/systems/metrics"
        assert request.get_header("Authorization") == "Bearer test-system-key"
        assert request.get_header("Content-type") == "application/json"

        import json

        body = json.loads(request.data.decode())
        assert set(body.keys()) == {"period_start", "period_end", "metrics"}
        assert set(body["metrics"].keys()) == {
            "acquisition", "revenue", "storefront", "platform_health", "inventory", "retention",
        }

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_a_rejected_report_fails_the_command_without_leaking_the_key(
        self, mock_urlopen, business, settings, caplog
    ):
        settings.GENMARS_SYSTEM_KEY = "super-secret-key"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.genmars.test/api/systems/metrics",
            401,
            "unauthorized",
            {},
            io.BytesIO(b"unauthorized"),
        )

        with pytest.raises(CommandError):
            call_command("report_commerce_metrics")

        assert "super-secret-key" not in caplog.text

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_a_network_error_fails_the_command_cleanly(self, mock_urlopen, business, settings):
        settings.GENMARS_SYSTEM_KEY = "test-system-key"
        mock_urlopen.side_effect = urllib.error.URLError("no route to host")

        with pytest.raises(CommandError):
            call_command("report_commerce_metrics")


@pytest.mark.django_db
def test_the_celery_task_never_raises_when_the_key_is_missing(business, settings):
    from analytics.tasks import report_commerce_metrics

    settings.GENMARS_SYSTEM_KEY = ""

    report_commerce_metrics()  # must not raise
