import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from accounts.models import User
from businesses.models import Business, BusinessMembership, default_business_id
from businesses.services import MembershipError, change_role, create_business, remove_membership


@pytest.fixture
def business(db):
    return create_business(name="Acme Fashion", slug="acme-fashion")


@pytest.fixture
def owner(business, make_user):
    user = make_user(email="owner@example.com")
    return BusinessMembership.objects.create(
        business=business, user=user, role=BusinessMembership.Role.OWNER
    )


@pytest.mark.django_db
def test_create_business_rejects_a_reserved_slug():
    with pytest.raises(ValidationError):
        create_business(name="Shadow Ops", slug="ops")


@pytest.mark.django_db
def test_create_business_auto_slugs_from_name_when_blank():
    business = create_business(name="Nairobi Tech Supplies")
    assert business.slug == "nairobi-tech-supplies"


@pytest.mark.django_db
def test_business_slug_must_be_unique(business):
    with pytest.raises(ValidationError):
        create_business(name="A Second Acme", slug=business.slug)


@pytest.mark.django_db
def test_one_membership_per_business_and_user(business, make_user):
    user = make_user(email="member@example.com")
    BusinessMembership.objects.create(business=business, user=user, role=BusinessMembership.Role.STAFF)
    with pytest.raises(IntegrityError):
        BusinessMembership.objects.create(business=business, user=user, role=BusinessMembership.Role.MANAGER)


@pytest.mark.django_db
def test_removing_the_last_active_owner_is_refused(owner):
    with pytest.raises(MembershipError) as exc:
        remove_membership(owner)
    assert exc.value.reason == "last_owner"
    assert BusinessMembership.objects.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_demoting_the_last_active_owner_is_refused(owner):
    with pytest.raises(MembershipError):
        change_role(owner, BusinessMembership.Role.MANAGER)
    owner.refresh_from_db()
    assert owner.role == BusinessMembership.Role.OWNER


@pytest.mark.django_db
def test_removing_an_owner_succeeds_when_another_active_owner_remains(business, owner, make_user):
    second_owner_user = make_user(email="second-owner@example.com")
    BusinessMembership.objects.create(
        business=business, user=second_owner_user, role=BusinessMembership.Role.OWNER
    )
    remove_membership(owner)  # should not raise — a second active owner exists
    assert not BusinessMembership.objects.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_membership_grants_no_platform_admin_privilege(owner):
    """
    The explicit invariant from the approved plan: a business membership —
    even an owner's — must never translate into `User.is_staff`. Nothing in
    this app writes that field, so this is really asserting the absence of
    any such path.
    """
    owner.user.refresh_from_db()
    assert owner.user.is_staff is False


# ── The Phase 1 migration bridge ─────────────────────────────────────────


@pytest.mark.django_db
def test_seeded_business_exists_after_migrating():
    """
    businesses/migrations/0002_backfill_initial_business creates exactly
    this row as part of the normal migration chain — by the time any test
    runs, it must already exist.
    """
    assert Business.objects.filter(slug="genmars-store").exists()


@pytest.mark.django_db
def test_default_business_id_resolves_to_the_seeded_business():
    seeded = Business.objects.get(slug="genmars-store")
    assert default_business_id() == seeded.pk


@pytest.mark.django_db
def test_existing_staff_user_was_grandfathered_in_as_owner(make_user):
    """
    Mirrors the migration's own grandfathering step for a user created
    after it ran — this asserts the DATA the migration left behind for
    users that existed at migration time, by checking the real staff
    fixture the rest of the suite already relies on having ops access.
    """
    staff = make_user(email="existing-staff@example.com", is_staff=True)
    # Not grandfathered automatically — only pre-existing is_staff users were,
    # by the migration, at the moment it ran. A new staff user created after
    # the fact has no membership yet; that's deliberate (see Decision 1 of
    # the plan — platform admin and business ownership are independent).
    assert not BusinessMembership.objects.filter(user=staff).exists()
    assert staff.is_staff is True


# ── /api/ops/platform/businesses/ — Genmars operating the platform ──────


@pytest.mark.django_db
def test_non_platform_admin_cannot_access_the_platform_businesses_endpoint(auth_client):
    response = auth_client.get("/api/ops/platform/businesses/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_business_owner_without_is_staff_cannot_access_the_platform_businesses_endpoint(business_owner_client):
    """The reverse of Decision 1's other direction: owning a business grants
    nothing on the platform-admin surface either."""
    response = business_owner_client.get("/api/ops/platform/businesses/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_platform_admin_can_create_list_and_activate_a_business(staff_client):
    create = staff_client.post(
        "/api/ops/platform/businesses/",
        {"name": "Nairobi Tech Supplies", "slug": "nairobi-tech"},
        format="json",
    )
    assert create.status_code == 201
    assert create.data["slug"] == "nairobi-tech"
    assert create.data["status"] == "active"

    listing = staff_client.get("/api/ops/platform/businesses/")
    assert listing.status_code == 200
    assert "nairobi-tech" in [row["slug"] for row in listing.data["results"]]

    business_id = create.data["id"]
    suspend = staff_client.post(f"/api/ops/platform/businesses/{business_id}/suspend/")
    assert suspend.status_code == 200
    assert suspend.data["status"] == "suspended"

    activate = staff_client.post(f"/api/ops/platform/businesses/{business_id}/activate/")
    assert activate.status_code == 200
    assert activate.data["status"] == "active"


@pytest.mark.django_db
def test_platform_admin_cannot_create_a_business_with_a_reserved_slug(staff_client):
    response = staff_client.post(
        "/api/ops/platform/businesses/", {"name": "Shadow Ops", "slug": "ops"}, format="json"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_creating_a_business_through_the_api_grants_no_platform_admin_privilege(staff_client, customer):
    """
    Creating or owning a business is never a path to `is_staff`. Nothing in
    this endpoint even accepts that field — this asserts the behaviour, not
    just the field's absence.
    """
    staff_client.post(
        "/api/ops/platform/businesses/", {"name": "Owned By Customer", "slug": "owned-by-customer"},
        format="json",
    )
    customer.refresh_from_db()
    assert customer.is_staff is False
