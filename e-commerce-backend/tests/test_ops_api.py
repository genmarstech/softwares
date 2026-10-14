import pytest

from orders.models import Order
from orders.services import create_order_from_cart

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)

OPS_ENDPOINTS = [
    "/api/ops/genmars-store/orders/",
    "/api/ops/genmars-store/payments/",
    "/api/ops/genmars-store/customers/",
    "/api/ops/genmars-store/dashboard/",
    "/api/ops/genmars-store/settings/",
]


@pytest.mark.django_db
@pytest.mark.parametrize("url", OPS_ENDPOINTS)
def test_non_staff_customer_is_forbidden_from_every_ops_endpoint(auth_client, url):
    response = auth_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("url", OPS_ENDPOINTS)
def test_platform_admin_without_business_membership_is_forbidden_from_every_ops_endpoint(staff_client, url):
    """Decision 1, exercised against the ops surface specifically:
    is_staff=True alone is not a BusinessMembership."""
    response = staff_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("url", OPS_ENDPOINTS)
def test_anonymous_visitor_is_rejected_from_every_ops_endpoint(api_client, url):
    response = api_client.get(url)
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_staff_sees_every_customers_order_not_just_their_own(business_owner_client, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(cart.user, cart, **DELIVERY)

    response = business_owner_client.get("/api/ops/genmars-store/orders/")
    assert response.status_code == 200
    references = [row["reference"] for row in response.data["results"]]
    assert order.reference in references

    detail = business_owner_client.get(f"/api/ops/genmars-store/orders/{order.id}/")
    assert detail.status_code == 200
    assert detail.data["items"][0]["product_name"] == product.name


@pytest.mark.django_db
def test_staff_can_transition_an_order_through_the_same_state_machine(business_owner_client, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(cart.user, cart, **DELIVERY)
    order.status = Order.Status.PAID
    order.save(update_fields=["status"])

    response = business_owner_client.post(
        f"/api/ops/genmars-store/orders/{order.id}/transition/", {"to_status": "processing"}
    )
    assert response.status_code == 200
    assert response.data["status"] == "processing"

    # Invalid transition is still rejected, same as the service layer.
    response = business_owner_client.post(
        f"/api/ops/genmars-store/orders/{order.id}/transition/", {"to_status": "awaiting_payment"}
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_ops_customers_lists_non_staff_users_with_order_stats(business_owner_client, customer, cart_with_item):
    cart, product, record = cart_with_item
    create_order_from_cart(cart.user, cart, **DELIVERY)

    response = business_owner_client.get("/api/ops/genmars-store/customers/")
    assert response.status_code == 200
    rows = {row["email"]: row for row in response.data["results"]}
    assert cart.user.email in rows
    assert rows[cart.user.email]["order_count"] == 1


@pytest.mark.django_db
def test_ops_dashboard_reports_sales_and_low_stock(business_owner_client, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(cart.user, cart, **DELIVERY)
    order.status = Order.Status.PAID
    order.save(update_fields=["status"])

    response = business_owner_client.get("/api/ops/genmars-store/dashboard/")
    assert response.status_code == 200
    assert float(response.data["sales_total"]) == float(order.total)
    assert response.data["orders_by_status"]["paid"] == 1
    assert len(response.data["recent_orders"]) == 1


@pytest.mark.django_db
def test_ops_settings_get_and_patch(business_owner_client):
    get_response = business_owner_client.get("/api/ops/genmars-store/settings/")
    assert get_response.status_code == 200
    assert get_response.data["currency"] == "KES"

    patch_response = business_owner_client.patch(
        "/api/ops/genmars-store/settings/", {"default_delivery_fee": "250.00"}
    )
    assert patch_response.status_code == 200
    assert patch_response.data["default_delivery_fee"] == "250.00"
