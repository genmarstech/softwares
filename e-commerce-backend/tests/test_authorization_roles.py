"""
The three business-level roles — owner, manager, staff — against every
endpoint the role split in businesses/permissions.py actually touches.
Platform-admin-vs-business-owner separation is covered in test_businesses.py
and test_tenant_isolation.py; this file is specifically about owner vs
manager vs staff within one business.
"""

import pytest

from businesses.models import BusinessMembership


# ── Settings — owner/manager only, staff excluded ────────────────────────


@pytest.mark.django_db
def test_staff_cannot_read_or_write_settings(business_staff_client):
    get_response = business_staff_client.get("/api/ops/genmars-store/settings/")
    assert get_response.status_code == 403

    patch_response = business_staff_client.patch(
        "/api/ops/genmars-store/settings/", {"default_delivery_fee": "100.00"}
    )
    assert patch_response.status_code == 403


@pytest.mark.django_db
def test_manager_can_read_and_write_settings(business_manager_client):
    get_response = business_manager_client.get("/api/ops/genmars-store/settings/")
    assert get_response.status_code == 200

    patch_response = business_manager_client.patch(
        "/api/ops/genmars-store/settings/", {"default_delivery_fee": "150.00"}
    )
    assert patch_response.status_code == 200
    assert patch_response.data["default_delivery_fee"] == "150.00"


# ── Catalog management — owner/manager write, any member reads ──────────


@pytest.mark.django_db
def test_staff_cannot_create_a_product_but_can_read_the_catalog(business_staff_client, product_with_stock):
    product_with_stock(status="draft", sku="DRAFT-1", name="Unlisted Thing")

    create = business_staff_client.post(
        "/api/genmars-store/products/", {"name": "x", "sku": "STAFF-TRY-1", "price": "1.00"}, format="json"
    )
    assert create.status_code == 403

    # Staff still sees this business's own drafts — "staff: read products".
    listing = business_staff_client.get("/api/genmars-store/products/")
    assert listing.status_code == 200
    assert listing.data["count"] == 1


@pytest.mark.django_db
def test_manager_can_create_and_edit_products(business_manager_client):
    create = business_manager_client.post(
        "/api/genmars-store/products/", {"name": "Manager Made", "sku": "MGR-1", "price": "50.00"}, format="json"
    )
    assert create.status_code == 201

    patch = business_manager_client.patch(
        f"/api/genmars-store/products/{create.data['id']}/", {"price": "60.00"}, format="json"
    )
    assert patch.status_code == 200


@pytest.mark.django_db
def test_staff_cannot_create_a_category(business_staff_client):
    response = business_staff_client.post("/api/genmars-store/categories/", {"name": "New Category"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_cannot_attach_a_product_image_but_can_list_them(business_staff_client, product_with_stock):
    product, _ = product_with_stock()
    response = business_staff_client.post(
        "/api/genmars-store/product-images/", {"product": product.id, "sort_order": 0}, format="json"
    )
    assert response.status_code == 403

    listing = business_staff_client.get("/api/genmars-store/product-images/")
    assert listing.status_code == 200


# ── Inventory — create/update/delete is manager-tier; adjust/read is staff's job ──


@pytest.mark.django_db
def test_staff_cannot_create_an_inventory_record_directly(business_staff_client, product_with_stock):
    product, _ = product_with_stock()
    # product_with_stock already created a record for this product; use a
    # second product with none to exercise the create path.
    from catalog.models import Product

    product2 = Product.objects.create(
        name="No Stock Yet", sku="NEEDS-RECORD-1", price="10.00",
        status=Product.Status.PUBLISHED, business=product.business,
    )
    response = business_staff_client.post(
        "/api/genmars-store/inventory/", {"product": product2.id, "quantity": 5}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_can_adjust_stock_and_read_movements(business_staff_client, product_with_stock):
    product, record = product_with_stock(quantity=10)

    adjust = business_staff_client.post(
        f"/api/genmars-store/inventory/{record.id}/adjust/", {"delta": -2, "reason": "order"}
    )
    assert adjust.status_code == 200
    assert adjust.data["quantity"] == 8

    movements = business_staff_client.get(f"/api/genmars-store/inventory/{record.id}/movements/")
    assert movements.status_code == 200
    assert len(movements.data) == 1


@pytest.mark.django_db
def test_manager_can_create_and_delete_an_inventory_record(business_manager_client, product_with_stock):
    product, _ = product_with_stock()
    from catalog.models import Product

    product2 = Product.objects.create(
        name="Manager Adds Stock", sku="MGR-STOCK-1", price="10.00",
        status=Product.Status.PUBLISHED, business=product.business,
    )
    create = business_manager_client.post(
        "/api/genmars-store/inventory/", {"product": product2.id, "quantity": 5}, format="json"
    )
    assert create.status_code == 201

    delete = business_manager_client.delete(f"/api/genmars-store/inventory/{create.data['id']}/")
    assert delete.status_code == 204


# ── Orders — transition is explicitly staff's fulfilment job ─────────────


@pytest.mark.django_db
def test_staff_can_transition_an_order(business_staff_client, cart_with_item):
    from orders.models import Order
    from orders.services import create_order_from_cart

    cart, product, record = cart_with_item
    order = create_order_from_cart(
        cart.user, cart,
        delivery_recipient_name="Ada", delivery_line1="1 Moi Ave",
        delivery_city="Nairobi", delivery_phone="0712345678",
    )
    order.status = Order.Status.PAID
    order.save(update_fields=["status"])

    response = business_staff_client.post(
        f"/api/ops/genmars-store/orders/{order.id}/transition/", {"to_status": "processing"}
    )
    assert response.status_code == 200


# ── Membership management — owner only for mutation, any member to read ──


@pytest.mark.django_db
def test_staff_and_manager_can_list_members_but_not_mutate(business_staff_client, business_manager_client, business_owner):
    staff_list = business_staff_client.get("/api/ops/genmars-store/members/")
    assert staff_list.status_code == 200

    manager_list = business_manager_client.get("/api/ops/genmars-store/members/")
    assert manager_list.status_code == 200

    for client in (business_staff_client, business_manager_client):
        response = client.post(
            "/api/ops/genmars-store/members/", {"user": business_owner.id, "role": "staff"}, format="json"
        )
        assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_invite_change_role_and_remove_a_member(business_owner_client, make_user, business):
    new_user = make_user(email="new-hire@example.com")

    create = business_owner_client.post(
        "/api/ops/genmars-store/members/", {"user": new_user.id, "role": "staff"}, format="json"
    )
    assert create.status_code == 201
    member_id = create.data["id"]

    promote = business_owner_client.patch(
        f"/api/ops/genmars-store/members/{member_id}/", {"role": "manager"}, format="json"
    )
    assert promote.status_code == 200
    assert promote.data["role"] == "manager"

    remove = business_owner_client.delete(f"/api/ops/genmars-store/members/{member_id}/")
    assert remove.status_code == 204
    assert not BusinessMembership.objects.filter(pk=member_id).exists()


@pytest.mark.django_db
def test_owner_cannot_remove_themselves_as_the_last_active_owner(business_owner_client, business_owner, business):
    membership = BusinessMembership.objects.get(business=business, user=business_owner)
    response = business_owner_client.delete(f"/api/ops/genmars-store/members/{membership.id}/")
    assert response.status_code == 400
    assert BusinessMembership.objects.filter(pk=membership.id).exists()


@pytest.mark.django_db
def test_adding_a_member_grants_no_platform_admin_privilege(business_owner_client, make_user):
    """Decision 1, through the membership-management endpoint specifically:
    nothing accepted here can ever set is_staff, even if offered in the
    payload."""
    new_user = make_user(email="cannot-become-admin@example.com")
    response = business_owner_client.post(
        "/api/ops/genmars-store/members/",
        {"user": new_user.id, "role": "manager", "is_staff": True},
        format="json",
    )
    assert response.status_code == 201
    new_user.refresh_from_db()
    assert new_user.is_staff is False


@pytest.mark.django_db
def test_cannot_add_a_duplicate_membership_for_the_same_user(business_owner_client, business_owner, business):
    response = business_owner_client.post(
        "/api/ops/genmars-store/members/", {"user": business_owner.id, "role": "manager"}, format="json"
    )
    assert response.status_code == 400
