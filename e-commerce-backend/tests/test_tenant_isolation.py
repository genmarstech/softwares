"""
Business A / business B isolation — the core promise of Phases 2 and 3.
Every test here constructs a resource under `second_business`
("acme-fashion") and proves it cannot be read, written, or counted through
`business` ("genmars-store")'s own URLs, API client, or aggregates, and vice
versa where relevant.

`Cart.user` was a `OneToOneField` (one cart per user, platform-wide) through
Phases 1-2, so a single customer couldn't hold carts at two businesses at
once — tests that needed two businesses' orders had to use two different
customers, or construct an order directly instead of through a cart. Phase
3 changed `Cart` to one row per `(user, business)`; the tests near the
bottom of this file (under "Multi-business customers") exercise the real
cart → checkout flow for one shared customer now that it's possible, rather
than the Phase 2 workaround.
"""

from decimal import Decimal

import pytest

from cart.models import Cart, CartItem
from orders.models import Order
from orders.services import create_order_from_cart
from payments.models import Payment

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


def _order_in_business(customer, business, product_qty_pair, **delivery_overrides):
    cart = Cart.objects.create(user=customer, business=business)
    product, _ = product_qty_pair
    CartItem.objects.create(cart=cart, product=product, quantity=1)
    delivery = {**DELIVERY, **delivery_overrides}
    return create_order_from_cart(customer, cart, **delivery)


# ── Catalog ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_business_b_product_is_invisible_in_business_as_product_list(
    api_client, product_with_stock, second_business
):
    product_with_stock(business=second_business, sku="B-SKU-1")
    response = api_client.get("/api/genmars-store/products/")
    assert response.status_code == 200
    assert response.data["count"] == 0


@pytest.mark.django_db
def test_business_a_owner_cannot_read_or_write_business_bs_product(
    business_owner_client, product_with_stock, second_business
):
    product, _ = product_with_stock(business=second_business, sku="B-SKU-1")

    detail = business_owner_client.get(f"/api/genmars-store/products/{product.id}/")
    assert detail.status_code == 404

    patch = business_owner_client.patch(
        f"/api/genmars-store/products/{product.id}/", {"price": "1.00"}, format="json"
    )
    assert patch.status_code == 404


@pytest.mark.django_db
def test_two_businesses_can_each_have_a_product_with_the_same_sku_and_name(
    business_owner_client, second_business_owner_client, second_business
):
    """
    catalog/models.py's uniqueness is per-business, not global (fixed in
    Phase 3 after this exact scenario collided in a test fixture) — two
    independent stores must each be free to name and SKU their own catalog
    however they like.
    """
    created_a = business_owner_client.post(
        "/api/genmars-store/products/",
        {"name": "Classic Tee", "sku": "SHARED-SKU", "price": "500.00"},
        format="json",
    )
    created_b = second_business_owner_client.post(
        f"/api/{second_business.slug}/products/",
        {"name": "Classic Tee", "sku": "SHARED-SKU", "price": "750.00"},
        format="json",
    )
    assert created_a.status_code == 201
    assert created_b.status_code == 201
    assert created_a.data["id"] != created_b.data["id"]


@pytest.mark.django_db
def test_duplicate_sku_within_the_same_business_is_still_rejected(business_owner_client, product_with_stock):
    product_with_stock(sku="DUP-SKU")
    response = business_owner_client.post(
        "/api/genmars-store/products/",
        {"name": "Another Name", "sku": "DUP-SKU", "price": "500.00"},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_two_businesses_can_each_have_a_variant_with_the_same_sku(
    business_owner_client, second_business_owner_client, product_with_stock, second_business
):
    product_a, _ = product_with_stock(sku="VAR-PARENT-A")
    product_b, _ = product_with_stock(business=second_business, sku="VAR-PARENT-B")

    created_a = business_owner_client.post(
        "/api/genmars-store/product-variants/",
        {"product": product_a.id, "name": "Size", "value": "M", "sku": "SHARED-VARIANT-SKU"},
        format="json",
    )
    created_b = second_business_owner_client.post(
        f"/api/{second_business.slug}/product-variants/",
        {"product": product_b.id, "name": "Size", "value": "M", "sku": "SHARED-VARIANT-SKU"},
        format="json",
    )
    assert created_a.status_code == 201
    assert created_b.status_code == 201


@pytest.mark.django_db
def test_duplicate_variant_sku_within_the_same_business_is_rejected(business_owner_client, product_with_stock):
    product_a, _ = product_with_stock(sku="VAR-PARENT-C")
    business_owner_client.post(
        "/api/genmars-store/product-variants/",
        {"product": product_a.id, "name": "Size", "value": "S", "sku": "DUP-VARIANT-SKU"},
        format="json",
    )
    response = business_owner_client.post(
        "/api/genmars-store/product-variants/",
        {"product": product_a.id, "name": "Size", "value": "L", "sku": "DUP-VARIANT-SKU"},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_attach_another_businesss_product_to_a_variant(
    business_owner_client, product_with_stock, second_business
):
    product, _ = product_with_stock(business=second_business, sku="B-SKU-1")
    response = business_owner_client.post(
        "/api/genmars-store/product-variants/",
        {"product": product.id, "name": "Size", "value": "M", "sku": "B-SKU-1-M"},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_attach_another_businesss_product_to_an_image(
    business_owner_client, product_with_stock, second_business
):
    product, _ = product_with_stock(business=second_business, sku="B-SKU-1")
    response = business_owner_client.post(
        "/api/genmars-store/product-images/", {"product": product.id, "sort_order": 0}, format="json"
    )
    assert response.status_code == 400


# ── Inventory ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_business_a_cannot_see_or_adjust_business_bs_inventory(
    business_owner_client, product_with_stock, second_business
):
    _, record = product_with_stock(business=second_business, sku="B-SKU-1")

    detail = business_owner_client.get(f"/api/genmars-store/inventory/{record.id}/")
    assert detail.status_code == 404

    adjust = business_owner_client.post(
        f"/api/genmars-store/inventory/{record.id}/adjust/", {"delta": 1, "reason": "restock"}
    )
    assert adjust.status_code == 404


# ── Cart ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_cannot_add_another_businesss_product_to_this_businesss_cart(
    auth_client, product_with_stock, second_business
):
    product, _ = product_with_stock(business=second_business, sku="B-SKU-1")
    response = auth_client.post(
        "/api/genmars-store/cart/items/", {"product": product.id, "quantity": 1}, format="json"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_patch_an_existing_cart_item_onto_another_businesss_product(
    auth_client, product_with_stock, second_business
):
    """
    The create path (test above) is only half the check — this is the one
    that was actually missing: CartItemViewSet.perform_create() enforced
    product.business == this business, but nothing stopped a PATCH/PUT to an
    EXISTING item from changing its `product` straight past that check
    (perform_update() has no override, so DRF's default just saves the
    serializer). Confirmed exploitable before the fix: a genmars-store cart
    item PATCHed to acme-fashion's product id survived to checkout, created
    a real genmars-store Order containing acme-fashion's product, and
    decremented acme-fashion's own stock for an order acme-fashion's own
    ops views would never show. The check now lives in
    CartItemSerializer.validate(), which both create and update run.
    """
    own_product, _ = product_with_stock(sku="A-SKU-OWN")
    other_product, _ = product_with_stock(business=second_business, sku="B-SKU-OTHER")

    add = auth_client.post(
        "/api/genmars-store/cart/items/", {"product": own_product.id, "quantity": 1}, format="json"
    )
    assert add.status_code == 201
    item_id = add.data["id"]

    patch = auth_client.patch(
        f"/api/genmars-store/cart/items/{item_id}/", {"product": other_product.id}, format="json"
    )
    assert patch.status_code == 400

    from cart.models import CartItem

    item = CartItem.objects.get(pk=item_id)
    assert item.product_id == own_product.id  # untouched


# ── Orders ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_business_a_ops_order_list_and_detail_exclude_business_bs_order(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="customer-b@example.com")
    order_b = _order_in_business(other_customer, second_business, product_with_stock(business=second_business, sku="B-SKU-1"))

    listing = business_owner_client.get("/api/ops/genmars-store/orders/")
    assert listing.status_code == 200
    assert order_b.reference not in [row["reference"] for row in listing.data["results"]]

    detail = business_owner_client.get(f"/api/ops/genmars-store/orders/{order_b.id}/")
    assert detail.status_code == 404


@pytest.mark.django_db
def test_customer_order_in_business_a_is_a_404_under_business_bs_slug(
    api_client, cart_with_item, second_business
):
    cart, product, record = cart_with_item  # business A
    order = create_order_from_cart(cart.user, cart, **DELIVERY)

    api_client.force_authenticate(user=cart.user)
    response = api_client.get(f"/api/{second_business.slug}/orders/{order.id}/")
    assert response.status_code == 404


# ── Payments ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_customer_cannot_pay_for_a_business_as_order_through_business_bs_initiate_url(
    api_client, cart_with_item, second_business
):
    cart, product, record = cart_with_item  # business A
    order = create_order_from_cart(cart.user, cart, **DELIVERY)

    api_client.force_authenticate(user=cart.user)
    response = api_client.post(
        f"/api/{second_business.slug}/payments/mpesa/initiate/",
        {"order_reference": order.reference, "phone": "0712345678"},
        format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_business_a_payments_list_excludes_business_bs_payment(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="customer-b2@example.com")
    order_b = _order_in_business(
        other_customer, second_business, product_with_stock(business=second_business, sku="B-SKU-1")
    )
    Payment.objects.create(
        order=order_b, amount=order_b.total, currency=order_b.currency,
        phone_number="254712345678", checkout_request_id="iso-test-b",
    )

    response = business_owner_client.get("/api/ops/genmars-store/payments/")
    assert response.status_code == 200
    assert response.data["count"] == 0


# ── Reports / dashboard ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_sales_and_status_counts_exclude_the_other_businesss_orders(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="customer-b3@example.com")
    order_b = _order_in_business(
        other_customer, second_business, product_with_stock(business=second_business, sku="B-SKU-1", price="999.00")
    )
    order_b.status = Order.Status.PAID
    order_b.save(update_fields=["status"])

    response = business_owner_client.get("/api/ops/genmars-store/dashboard/")
    assert response.status_code == 200
    assert float(response.data["sales_total"]) == 0.0
    assert response.data["orders_by_status"] == {}
    assert response.data["recent_orders"] == []


# ── Settings ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_settings_changes_do_not_cross_businesses(
    business_owner_client, second_business_owner_client, second_business
):
    patch = business_owner_client.patch("/api/ops/genmars-store/settings/", {"default_delivery_fee": "300.00"})
    assert patch.status_code == 200

    other = second_business_owner_client.get(f"/api/ops/{second_business.slug}/settings/")
    assert other.status_code == 200
    assert other.data["default_delivery_fee"] == "0.00"


@pytest.mark.django_db
def test_business_owner_cannot_read_another_businesss_settings(business_owner_client, second_business):
    response = business_owner_client.get(f"/api/ops/{second_business.slug}/settings/")
    assert response.status_code == 403


# ── Customers (Decision 2: one account, many businesses) ────────────────


@pytest.mark.django_db
def test_ops_customer_list_excludes_a_customer_who_only_ordered_from_another_business(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="only-business-b@example.com")
    _order_in_business(other_customer, second_business, product_with_stock(business=second_business, sku="B-SKU-1"))

    response = business_owner_client.get("/api/ops/genmars-store/customers/")
    assert response.status_code == 200
    assert "only-business-b@example.com" not in [row["email"] for row in response.data["results"]]


@pytest.mark.django_db
def test_ops_customer_stats_reflect_only_this_businesss_orders_for_a_shared_customer(
    business_owner_client, api_client, customer, product_with_stock, second_business
):
    """One account shopping at two businesses (Decision 2), through the real
    cart → checkout flow at each — unblocked by Phase 3's cart change."""
    product_a, _ = product_with_stock(sku="A-SKU-1")
    product_b, _ = product_with_stock(business=second_business, sku="B-SKU-1")

    api_client.force_authenticate(user=customer)
    api_client.post("/api/genmars-store/cart/items/", {"product": product_a.id, "quantity": 1}, format="json")
    order_a = api_client.post("/api/genmars-store/checkout/", DELIVERY, format="json").data

    api_client.post(
        f"/api/{second_business.slug}/cart/items/", {"product": product_b.id, "quantity": 1}, format="json"
    )
    api_client.post(f"/api/{second_business.slug}/checkout/", DELIVERY, format="json")

    response = business_owner_client.get("/api/ops/genmars-store/customers/")
    assert response.status_code == 200
    row = next(r for r in response.data["results"] if r["email"] == customer.email)
    assert row["order_count"] == 1
    assert Decimal(row["lifetime_total"]) == Decimal(order_a["total"])


# ── Multi-business customers (Phase 3: per-(user, business) carts) ──────


@pytest.mark.django_db
def test_customer_can_hold_separate_carts_at_two_businesses(
    api_client, customer, product_with_stock, second_business
):
    product_a, _ = product_with_stock(sku="A-SKU-1")
    product_b, _ = product_with_stock(business=second_business, sku="B-SKU-1")
    api_client.force_authenticate(user=customer)

    add_a = api_client.post(
        "/api/genmars-store/cart/items/", {"product": product_a.id, "quantity": 2}, format="json"
    )
    add_b = api_client.post(
        f"/api/{second_business.slug}/cart/items/", {"product": product_b.id, "quantity": 3}, format="json"
    )
    assert add_a.status_code == 201
    assert add_b.status_code == 201

    cart_a = api_client.get("/api/genmars-store/cart/")
    cart_b = api_client.get(f"/api/{second_business.slug}/cart/")
    assert cart_a.status_code == 200
    assert cart_b.status_code == 200

    assert [item["product"] for item in cart_a.data["items"]] == [product_a.id]
    assert cart_a.data["items"][0]["quantity"] == 2
    assert [item["product"] for item in cart_b.data["items"]] == [product_b.id]
    assert cart_b.data["items"][0]["quantity"] == 3


@pytest.mark.django_db
def test_same_customer_checks_out_independently_at_two_businesses(
    api_client, customer, product_with_stock, second_business
):
    product_a, record_a = product_with_stock(sku="A-SKU-1")
    product_b, record_b = product_with_stock(business=second_business, sku="B-SKU-1")
    api_client.force_authenticate(user=customer)

    api_client.post("/api/genmars-store/cart/items/", {"product": product_a.id, "quantity": 1}, format="json")
    api_client.post(
        f"/api/{second_business.slug}/cart/items/", {"product": product_b.id, "quantity": 1}, format="json"
    )

    order_a = api_client.post("/api/genmars-store/checkout/", DELIVERY, format="json")
    order_b = api_client.post(f"/api/{second_business.slug}/checkout/", DELIVERY, format="json")

    assert order_a.status_code == 201
    assert order_b.status_code == 201
    assert order_a.data["reference"] != order_b.data["reference"]

    record_a.refresh_from_db()
    record_b.refresh_from_db()
    assert record_a.quantity == 9  # 10 - 1, business A's own stock
    assert record_b.quantity == 9  # 10 - 1, business B's own stock — unaffected by A's checkout


# ── Resolution itself ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_unknown_business_slug_is_a_404_not_a_500(api_client):
    response = api_client.get("/api/no-such-business/products/")
    assert response.status_code == 404
