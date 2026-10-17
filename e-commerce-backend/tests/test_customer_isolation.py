"""
Phase 5 audit: cross-CUSTOMER isolation within the SAME business — distinct
from tests/test_object_id_substitution.py and tests/test_tenant_isolation.py,
which both prove isolation between two BUSINESSES. Two customers who both
shop at the same business is the more common real-world case and wasn't
separately covered for cart items or the checkout address_id path.
"""

import pytest

from accounts.models import Address
from cart.models import Cart, CartItem
from catalog.models import Product
from inventory.models import InventoryRecord

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


@pytest.mark.django_db
def test_customer_cannot_read_update_or_delete_another_customers_cart_item_in_the_same_business(
    api_client, make_user, product_with_stock
):
    product, _ = product_with_stock()
    owner = make_user(email="cart-owner@example.com")
    cart = Cart.objects.create(user=owner, business=product.business)
    item = CartItem.objects.create(cart=cart, product=product, quantity=1)

    other = make_user(email="cart-snooper@example.com")
    api_client.force_authenticate(user=other)

    get_resp = api_client.get(f"/api/genmars-store/cart/items/{item.id}/")
    patch_resp = api_client.patch(
        f"/api/genmars-store/cart/items/{item.id}/", {"quantity": 9}, format="json"
    )
    delete_resp = api_client.delete(f"/api/genmars-store/cart/items/{item.id}/")

    # 404, not 403 — same enumeration-oracle reasoning as everywhere else:
    # a cart item that exists but isn't the caller's must look identical to
    # one that doesn't exist.
    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    item.refresh_from_db()
    assert item.quantity == 1  # untouched


@pytest.mark.django_db
def test_customer_cannot_checkout_using_another_customers_saved_address(
    api_client, customer, make_user, product_with_stock
):
    product, record = product_with_stock(quantity=5)
    other = make_user(email="address-owner@example.com")
    other_address = Address.objects.create(
        user=other,
        recipient_name="Someone Else",
        line1="456 Other Rd",
        city="Mombasa",
        phone_number="0799999999",
    )

    cart = Cart.objects.create(user=customer, business=product.business)
    CartItem.objects.create(cart=cart, product=product, quantity=1)
    api_client.force_authenticate(user=customer)

    response = api_client.post(
        "/api/genmars-store/checkout/", {"address_id": other_address.id}, format="json"
    )

    # get_object_or_404(Address, pk=..., user=request.user) in
    # orders/views.py::CheckoutView — a 404, never a 403 or a 201 using
    # someone else's delivery details.
    assert response.status_code == 404
    record.refresh_from_db()
    assert record.quantity == 5  # no order created, no stock touched
    assert not Address.objects.get(pk=other_address.id).line1 == ""  # untouched, sanity check


@pytest.mark.django_db
def test_customer_can_checkout_using_their_own_saved_address(api_client, customer, product_with_stock):
    product, record = product_with_stock(quantity=5)
    own_address = Address.objects.create(
        user=customer,
        recipient_name="Ada Lovelace",
        line1="123 Moi Ave",
        city="Nairobi",
        phone_number="0712345678",
    )
    cart = Cart.objects.create(user=customer, business=product.business)
    CartItem.objects.create(cart=cart, product=product, quantity=1)
    api_client.force_authenticate(user=customer)

    response = api_client.post(
        "/api/genmars-store/checkout/", {"address_id": own_address.id}, format="json"
    )
    assert response.status_code == 201
    assert response.data["delivery_line1"] == "123 Moi Ave"
