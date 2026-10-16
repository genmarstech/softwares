"""
Confirms each storefront-funnel hook actually fires on the right request
and writes the right event_type/business/product — and that a logging
failure in analytics.services.record() never propagates (see that module's
own docstring on why).
"""

import pytest

from analytics.models import StorefrontEvent
from analytics.services import record


@pytest.mark.django_db
def test_listing_products_logs_a_visit(api_client, business, product_with_stock):
    product_with_stock()

    response = api_client.get(f"/api/{business.slug}/products/")

    assert response.status_code == 200
    assert StorefrontEvent.objects.filter(
        business=business, event_type=StorefrontEvent.EventType.VISIT
    ).count() == 1
    event = StorefrontEvent.objects.get(event_type=StorefrontEvent.EventType.VISIT)
    assert event.product is None


@pytest.mark.django_db
def test_retrieving_a_product_logs_a_product_view(api_client, business, product_with_stock):
    product, _record = product_with_stock()

    response = api_client.get(f"/api/{business.slug}/products/{product.id}/")

    assert response.status_code == 200
    event = StorefrontEvent.objects.get(event_type=StorefrontEvent.EventType.PRODUCT_VIEW)
    assert event.business_id == business.id
    assert event.product_id == product.id


@pytest.mark.django_db
def test_adding_to_cart_logs_add_to_cart(auth_client, business, product_with_stock):
    product, _record = product_with_stock()

    response = auth_client.post(
        f"/api/{business.slug}/cart/items/", {"product": product.id, "quantity": 1}
    )

    assert response.status_code == 201
    event = StorefrontEvent.objects.get(event_type=StorefrontEvent.EventType.ADD_TO_CART)
    assert event.business_id == business.id
    assert event.product_id == product.id


@pytest.mark.django_db
def test_adding_an_existing_cart_item_again_still_logs_an_event(auth_client, business, product_with_stock):
    """perform_create() has two branches — a brand-new CartItem, and
    bumping the quantity on one that already exists. Both are still a real
    add-to-cart from the customer's point of view."""
    product, _record = product_with_stock()
    auth_client.post(f"/api/{business.slug}/cart/items/", {"product": product.id, "quantity": 1})

    response = auth_client.post(
        f"/api/{business.slug}/cart/items/", {"product": product.id, "quantity": 1}
    )

    assert response.status_code == 201
    assert StorefrontEvent.objects.filter(event_type=StorefrontEvent.EventType.ADD_TO_CART).count() == 2


@pytest.mark.django_db
def test_checkout_logs_checkout_start(auth_client, business, cart_with_item):
    cart, product, _record = cart_with_item

    response = auth_client.post(
        f"/api/{business.slug}/checkout/",
        {
            "delivery_recipient_name": "Jane Doe",
            "delivery_line1": "123 Example Rd",
            "delivery_city": "Nairobi",
            "delivery_phone": "254712345678",
            "contact_phone": "254712345678",
        },
    )

    assert response.status_code == 201
    event = StorefrontEvent.objects.get(event_type=StorefrontEvent.EventType.CHECKOUT_START)
    assert event.business_id == business.id
    # Order-level, not product-level — a checkout can cover several lines.
    assert event.product is None


@pytest.mark.django_db
def test_checkout_failure_does_not_log_checkout_start(auth_client, business, customer):
    """An empty cart can't become an order — create_order_from_cart() raises
    OrderError and the view returns 400 before the analytics hook runs."""
    response = auth_client.post(
        f"/api/{business.slug}/checkout/",
        {
            "delivery_recipient_name": "Jane Doe",
            "delivery_line1": "123 Example Rd",
            "delivery_city": "Nairobi",
            "delivery_phone": "254712345678",
            "contact_phone": "254712345678",
        },
    )

    assert response.status_code == 400
    assert not StorefrontEvent.objects.filter(event_type=StorefrontEvent.EventType.CHECKOUT_START).exists()


@pytest.mark.django_db
def test_record_swallows_an_error_instead_of_raising(business):
    """A business with no pk at all (never saved) makes the FK insert fail
    at the database level — record() must log that and return, never let
    it propagate into whatever page/cart/checkout action triggered it."""
    unsaved_business = business.__class__(name="Unsaved", slug="unsaved-biz")

    record(unsaved_business, StorefrontEvent.EventType.VISIT)  # must not raise

    assert not StorefrontEvent.objects.filter(event_type=StorefrontEvent.EventType.VISIT).exists()
