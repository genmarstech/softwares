"""
Phase 6 audit: there is no API path for bootstrapping a BRAND NEW business
— creating the Business row, its first owner membership, and its first
products — because every one of those write paths requires an
authorization the business doesn't have anyone holding yet (IsBusinessOwner
for /api/ops/<slug>/members/, IsBusinessManager for the catalog). Django
admin is the only place that bootstrap can happen without a direct database
edit. These tests prove that path is real and currently working, not just
assumed from reading admin.py — the shortest safe manual onboarding
procedure this review documents depends on it.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from businesses.models import Business, BusinessMembership
from catalog.models import Product

User = get_user_model()


@pytest.fixture
def admin_client(db):
    superuser = User.objects.create_superuser(email="platform-admin@example.com", password="a-strong-passw0rd!")
    client = Client()
    client.force_login(superuser)
    return client, superuser


@pytest.mark.django_db
def test_createsuperuser_produces_a_real_admin_login(admin_client):
    """
    Not asserting on /admin/'s rendered page here — that template pulls in
    admin/css/base.css through WhiteNoise's CompressedManifestStaticFilesStorage,
    which needs `manage.py collectstatic` to have run first (a real
    deployment always runs this; a bare test checkout hasn't). The
    create_business/add_membership/create_product tests below each POST to
    an admin view and follow its redirect, which proves the authenticated,
    permission-checked path works without depending on that manifest.
    """
    client, superuser = admin_client
    assert superuser.is_staff is True
    assert superuser.is_superuser is True
    assert client.session["_auth_user_id"] == str(superuser.pk)


@pytest.mark.django_db
def test_platform_admin_can_create_a_brand_new_business_via_admin(admin_client):
    client, _ = admin_client
    response = client.post(
        reverse("admin:businesses_business_add"),
        {
            "name": "Pilot Business Co",
            "slug": "pilot-business-co",
            "status": Business.Status.ACTIVE,
            "country": "KE",
            "timezone": "Africa/Nairobi",
            "default_currency": "KES",
            "contact_email": "owner@pilot-business.example",
            "contact_phone": "",
            # Inline BusinessMembership formset management form, required
            # even with zero extra rows — Django admin's own inline contract.
            "memberships-TOTAL_FORMS": "0",
            "memberships-INITIAL_FORMS": "0",
            "memberships-MIN_NUM_FORMS": "0",
            "memberships-MAX_NUM_FORMS": "1000",
        },
    )
    assert response.status_code == 302, getattr(response, "context", {}).get("errors", response.content[:500])
    assert Business.objects.filter(slug="pilot-business-co").exists()


@pytest.mark.django_db
def test_platform_admin_can_assign_the_first_owner_via_the_business_admin_inline(admin_client, make_user):
    """
    The actual bootstrap step nothing else in this app can do: a business
    with zero memberships getting its first one, inline on the SAME admin
    page that created it (businesses/admin.py::BusinessMembershipInline).
    """
    client, _ = admin_client
    owner = make_user(email="pilot-owner@example.com")
    business = Business.objects.create(name="Pilot Business Co", slug="pilot-business-co-2")

    response = client.post(
        reverse("admin:businesses_business_change", args=[business.pk]),
        {
            "name": business.name,
            "slug": business.slug,
            "status": Business.Status.ACTIVE,
            "country": "KE",
            "timezone": "Africa/Nairobi",
            "default_currency": "KES",
            "contact_email": "",
            "contact_phone": "",
            "memberships-TOTAL_FORMS": "1",
            "memberships-INITIAL_FORMS": "0",
            "memberships-MIN_NUM_FORMS": "0",
            "memberships-MAX_NUM_FORMS": "1000",
            "memberships-0-business": business.pk,
            "memberships-0-user": owner.pk,
            "memberships-0-role": BusinessMembership.Role.OWNER,
            "memberships-0-status": BusinessMembership.MembershipStatus.ACTIVE,
        },
    )
    assert response.status_code == 302, response.context["inline_admin_formsets"][0].formset.errors if response.context else response.content[:500]
    assert BusinessMembership.objects.filter(business=business, user=owner, role="owner").exists()


@pytest.mark.django_db
def test_platform_admin_can_create_a_product_for_a_business_via_admin(admin_client):
    client, _ = admin_client
    business = Business.objects.create(name="Pilot Business Co", slug="pilot-business-co-3")

    response = client.post(
        reverse("admin:catalog_product_add"),
        {
            "business": business.pk,
            "name": "First Pilot Product",
            "slug": "first-pilot-product",
            "description": "",
            "sku": "PILOT-SKU-1",
            "price": "1000.00",
            "status": Product.Status.PUBLISHED,
            "category": "",
            "images-TOTAL_FORMS": "0",
            "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "1000",
            "variants-TOTAL_FORMS": "0",
            "variants-INITIAL_FORMS": "0",
            "variants-MIN_NUM_FORMS": "0",
            "variants-MAX_NUM_FORMS": "1000",
        },
    )
    assert response.status_code == 302, response.content[:500]
    assert Product.objects.filter(business=business, sku="PILOT-SKU-1").exists()
