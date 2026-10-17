"""
Cross-tenant object-ID substitution — not list endpoints. For every
resource with a detail URL (retrieve/update/delete, or a detail action),
this proves that an authenticated, legitimately-membered business A caller
cannot read, write, or delete business B's row by swapping in B's real
object id under A's own (valid, active) business slug.

The business slug resolving successfully is never the authorization check
— every test here uses a slug that DOES exist and IS active
(`business_owner_client` acting on `genmars-store`), to prove the slug's
validity alone grants nothing; the membership/ownership check underneath
it is what's actually being exercised.
"""

from decimal import Decimal

import pytest

from businesses.models import BusinessMembership
from cart.models import Cart, CartItem
from catalog.models import Category, Product, ProductImage, ProductVariant
from inventory.models import InventoryRecord
from orders.models import Order
from orders.services import create_order_from_cart
from payments.models import Payment

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


# ── Categories ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_category_detail_write_and_delete_blocked_by_id_substitution(business_owner_client, second_business):
    category_b = Category.objects.create(business=second_business, name="Shoes")

    get_resp = business_owner_client.get(f"/api/genmars-store/categories/{category_b.id}/")
    patch_resp = business_owner_client.patch(
        f"/api/genmars-store/categories/{category_b.id}/", {"name": "Hijacked"}, format="json"
    )
    delete_resp = business_owner_client.delete(f"/api/genmars-store/categories/{category_b.id}/")

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    category_b.refresh_from_db()
    assert category_b.name == "Shoes"  # untouched


# ── Products ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_product_delete_blocked_by_id_substitution(business_owner_client, product_with_stock, second_business):
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-PRODUCT-B")
    response = business_owner_client.delete(f"/api/genmars-store/products/{product_b.id}/")
    assert response.status_code == 404
    assert Product.objects.filter(pk=product_b.id).exists()


# ── Product images / variants ───────────────────────────────────────────


@pytest.mark.django_db
def test_product_image_detail_write_and_delete_blocked_by_id_substitution(
    business_owner_client, product_with_stock, second_business
):
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-IMG-B")
    image_b = ProductImage.objects.create(product=product_b, sort_order=0)

    get_resp = business_owner_client.get(f"/api/genmars-store/product-images/{image_b.id}/")
    patch_resp = business_owner_client.patch(
        f"/api/genmars-store/product-images/{image_b.id}/", {"sort_order": 9}, format="json"
    )
    delete_resp = business_owner_client.delete(f"/api/genmars-store/product-images/{image_b.id}/")

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    assert ProductImage.objects.filter(pk=image_b.id).exists()


@pytest.mark.django_db
def test_cannot_patch_an_existing_product_images_product_onto_another_businesss_product(
    business_owner_client, product_with_stock, second_business
):
    """
    perform_create() alone checked product.business — nothing stopped a
    PATCH to an existing, own-business image from reassigning `product`
    straight onto another business's product instead. Same class of bug as
    the cart-item one; fixed in ProductImageSerializer.validate_product().
    """
    own_product, _ = product_with_stock(sku="SUBST-IMG-OWN")
    other_product, _ = product_with_stock(business=second_business, sku="SUBST-IMG-OTHER")
    image = ProductImage.objects.create(product=own_product, sort_order=0)

    response = business_owner_client.patch(
        f"/api/genmars-store/product-images/{image.id}/", {"product": other_product.id}, format="json"
    )
    assert response.status_code == 400
    image.refresh_from_db()
    assert image.product_id == own_product.id


@pytest.mark.django_db
def test_product_variant_detail_write_and_delete_blocked_by_id_substitution(
    business_owner_client, product_with_stock, second_business
):
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-VAR-B")
    variant_b = ProductVariant.objects.create(
        product=product_b, business=second_business, name="Size", value="M", sku="SUBST-VAR-B-M"
    )

    get_resp = business_owner_client.get(f"/api/genmars-store/product-variants/{variant_b.id}/")
    patch_resp = business_owner_client.patch(
        f"/api/genmars-store/product-variants/{variant_b.id}/", {"value": "L"}, format="json"
    )
    delete_resp = business_owner_client.delete(f"/api/genmars-store/product-variants/{variant_b.id}/")

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    variant_b.refresh_from_db()
    assert variant_b.value == "M"


@pytest.mark.django_db
def test_cannot_patch_an_existing_variants_product_onto_another_businesss_product(
    business_owner_client, product_with_stock, second_business
):
    """Same fix as the ProductImage case above, applied to ProductVariant."""
    own_product, _ = product_with_stock(sku="SUBST-VAR-OWN")
    other_product, _ = product_with_stock(business=second_business, sku="SUBST-VAR-OTHER")
    variant = ProductVariant.objects.create(
        product=own_product, business=own_product.business, name="Size", value="M", sku="SUBST-VAR-OWN-M"
    )

    response = business_owner_client.patch(
        f"/api/genmars-store/product-variants/{variant.id}/", {"product": other_product.id}, format="json"
    )
    assert response.status_code == 400
    variant.refresh_from_db()
    assert variant.product_id == own_product.id


# ── Inventory ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_inventory_record_write_delete_and_movements_blocked_by_id_substitution(
    business_owner_client, product_with_stock, second_business
):
    _, record_b = product_with_stock(business=second_business, sku="SUBST-INV-B", quantity=10)

    patch_resp = business_owner_client.patch(
        f"/api/genmars-store/inventory/{record_b.id}/", {"low_stock_threshold": 1}, format="json"
    )
    delete_resp = business_owner_client.delete(f"/api/genmars-store/inventory/{record_b.id}/")
    movements_resp = business_owner_client.get(f"/api/genmars-store/inventory/{record_b.id}/movements/")
    adjust_resp = business_owner_client.post(
        f"/api/genmars-store/inventory/{record_b.id}/adjust/", {"delta": -1, "reason": "order"}
    )

    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    assert movements_resp.status_code == 404
    assert adjust_resp.status_code == 404
    record_b.refresh_from_db()
    assert record_b.quantity == 10  # untouched — the adjust attempt never ran


@pytest.mark.django_db
def test_cannot_patch_an_existing_inventory_records_product_onto_another_businesss_product(
    business_owner_client, product_with_stock, second_business
):
    """
    perform_create() alone checked product/variant business ownership —
    nothing stopped a PATCH to an existing, own-business InventoryRecord
    from reassigning `product` onto another business's product, which would
    hijack that other business's stock tracking under this business's
    record. Fixed in InventoryRecordSerializer.validate().
    """
    own_product, own_record = product_with_stock(sku="SUBST-INV-OWN")
    other_product, _ = product_with_stock(business=second_business, sku="SUBST-INV-OTHER")

    response = business_owner_client.patch(
        f"/api/genmars-store/inventory/{own_record.id}/", {"product": other_product.id}, format="json"
    )
    assert response.status_code == 400
    own_record.refresh_from_db()
    assert own_record.product_id == own_product.id


# ── Cart items ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_cart_item_write_and_delete_blocked_by_id_substitution_across_businesses(
    auth_client, customer, product_with_stock, second_business
):
    """
    Not "another customer's cart item" (that's ownership, already covered
    elsewhere) — this is the SAME customer's own cart item, but reached
    through the WRONG business's slug. cart/items/ is queryset-scoped by
    `cart__business`, so this id simply isn't in that business's queryset.
    """
    product_a, _ = product_with_stock(sku="SUBST-CART-A")
    auth_client.post("/api/genmars-store/cart/items/", {"product": product_a.id, "quantity": 1}, format="json")
    item_a = CartItem.objects.get(cart__user=customer, cart__business__slug="genmars-store")

    patch_resp = auth_client.patch(
        f"/api/{second_business.slug}/cart/items/{item_a.id}/", {"quantity": 5}, format="json"
    )
    delete_resp = auth_client.delete(f"/api/{second_business.slug}/cart/items/{item_a.id}/")

    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    item_a.refresh_from_db()
    assert item_a.quantity == 1


# ── Orders (customer-facing) ─────────────────────────────────────────────


@pytest.mark.django_db
def test_customer_cancel_action_blocked_by_id_substitution_across_businesses(
    api_client, cart_with_item, second_business
):
    cart, product, record = cart_with_item  # business A
    order = create_order_from_cart(cart.user, cart, **DELIVERY)
    api_client.force_authenticate(user=cart.user)

    response = api_client.post(f"/api/{second_business.slug}/orders/{order.id}/cancel/")
    assert response.status_code == 404
    order.refresh_from_db()
    assert order.status == Order.Status.AWAITING_PAYMENT


# ── Orders (ops) ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ops_order_transition_blocked_by_id_substitution(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="subst-order-b@example.com")
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-ORDER-B")
    cart_b = Cart.objects.create(user=other_customer, business=second_business)
    CartItem.objects.create(cart=cart_b, product=product_b, quantity=1)
    order_b = create_order_from_cart(other_customer, cart_b, **DELIVERY)
    order_b.status = Order.Status.PAID
    order_b.save(update_fields=["status"])

    response = business_owner_client.post(
        f"/api/ops/genmars-store/orders/{order_b.id}/transition/", {"to_status": "processing"}
    )
    assert response.status_code == 404
    order_b.refresh_from_db()
    assert order_b.status == Order.Status.PAID  # untouched


# ── Payments (ops) ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ops_payment_detail_blocked_by_id_substitution(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="subst-payment-b@example.com")
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-PAYMENT-B")
    cart_b = Cart.objects.create(user=other_customer, business=second_business)
    CartItem.objects.create(cart=cart_b, product=product_b, quantity=1)
    order_b = create_order_from_cart(other_customer, cart_b, **DELIVERY)
    payment_b = Payment.objects.create(
        order=order_b, amount=order_b.total, currency=order_b.currency,
        phone_number="254712345678", checkout_request_id="subst-payment-b",
    )

    response = business_owner_client.get(f"/api/ops/genmars-store/payments/{payment_b.id}/")
    assert response.status_code == 404


# ── Customers (ops) ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ops_customer_detail_blocked_by_id_substitution(
    business_owner_client, make_user, product_with_stock, second_business
):
    other_customer = make_user(email="subst-customer-b@example.com")
    product_b, _ = product_with_stock(business=second_business, sku="SUBST-CUSTOMER-B")
    cart_b = Cart.objects.create(user=other_customer, business=second_business)
    CartItem.objects.create(cart=cart_b, product=product_b, quantity=1)
    create_order_from_cart(other_customer, cart_b, **DELIVERY)

    response = business_owner_client.get(f"/api/ops/genmars-store/customers/{other_customer.id}/")
    assert response.status_code == 404


# ── Business memberships ────────────────────────────────────────────────


@pytest.mark.django_db
def test_membership_detail_write_and_delete_blocked_by_id_substitution(
    business_owner_client, second_business_owner, second_business
):
    membership_b = BusinessMembership.objects.get(business=second_business, user=second_business_owner)

    get_resp = business_owner_client.get(f"/api/ops/genmars-store/members/{membership_b.id}/")
    patch_resp = business_owner_client.patch(
        f"/api/ops/genmars-store/members/{membership_b.id}/", {"role": "staff"}, format="json"
    )
    delete_resp = business_owner_client.delete(f"/api/ops/genmars-store/members/{membership_b.id}/")

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert delete_resp.status_code == 404
    membership_b.refresh_from_db()
    assert membership_b.role == BusinessMembership.Role.OWNER


# ── Business slug never substitutes for authorization ────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/genmars-store/products/", {"name": "x", "sku": "slug-is-not-auth-1", "price": "1.00"}),
        ("patch", "/api/ops/genmars-store/settings/", {"default_delivery_fee": "1.00"}),
        ("post", "/api/ops/genmars-store/members/", {"user": 1, "role": "owner"}),
    ],
)
def test_a_valid_active_business_slug_grants_nothing_without_membership(auth_client, method, path, body):
    """The slug in every path above is real and active — a customer with
    zero membership anywhere still gets 403, not 200, on every one."""
    response = getattr(auth_client, method)(path, body, format="json")
    assert response.status_code == 403
