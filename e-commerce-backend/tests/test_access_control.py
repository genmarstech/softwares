import pytest

from orders.services import create_order_from_cart

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


@pytest.mark.django_db
def test_anonymous_visitor_can_browse_published_products(api_client, product_with_stock):
    product_with_stock()
    response = api_client.get("/api/products/")
    assert response.status_code == 200
    assert response.data["count"] == 1


@pytest.mark.django_db
def test_anonymous_visitor_cannot_write_products(api_client):
    response = api_client.post(
        "/api/products/", {"name": "x", "sku": "x", "price": "1.00"}, format="json"
    )
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_non_staff_customer_cannot_create_products(auth_client):
    response = auth_client.post(
        "/api/products/", {"name": "x", "sku": "x", "price": "1.00"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_customer_cannot_access_another_customers_order(api_client, make_user, cart_with_item):
    cart, product, record = cart_with_item
    owner = cart.user
    order = create_order_from_cart(owner, cart, **DELIVERY)

    other = make_user(email="someone-else@example.com")
    api_client.force_authenticate(user=other)

    response = api_client.get(f"/api/orders/{order.id}/")
    # 404, never 403 — a 403 would confirm the id exists for someone.
    assert response.status_code == 404


@pytest.mark.django_db
def test_owner_can_access_their_own_order(api_client, cart_with_item):
    cart, product, record = cart_with_item
    owner = cart.user
    order = create_order_from_cart(owner, cart, **DELIVERY)

    api_client.force_authenticate(user=owner)
    response = api_client.get(f"/api/orders/{order.id}/")
    assert response.status_code == 200
    assert response.data["reference"] == order.reference


@pytest.mark.django_db
def test_checkout_rejects_negative_or_zero_price_product(staff_client):
    response = staff_client.post(
        "/api/products/",
        {"name": "Bad Price", "sku": "BAD-1", "price": "0.00", "status": "published"},
        format="json",
    )
    assert response.status_code == 400
