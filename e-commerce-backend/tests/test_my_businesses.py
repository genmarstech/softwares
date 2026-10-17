"""
GET /api/me/businesses/ — added in response to a confirmed frontend-side
gap (no endpoint let a logged-in user discover their own businesses).
Filtered by `user=request.user` only; these tests exist specifically to
prove that filter can't be widened by anything the request carries.
"""

import pytest

from businesses.models import BusinessMembership


@pytest.mark.django_db
def test_requires_authentication(api_client):
    response = api_client.get("/api/me/businesses/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_user_with_no_memberships_gets_an_empty_list(auth_client):
    response = auth_client.get("/api/me/businesses/")
    assert response.status_code == 200
    assert response.data == []


@pytest.mark.django_db
def test_lists_the_callers_own_membership_with_business_details(business_owner_client, business):
    response = business_owner_client.get("/api/me/businesses/")
    assert response.status_code == 200
    assert len(response.data) == 1
    row = response.data[0]
    assert row["role"] == "owner"
    assert row["status"] == "active"
    assert row["business"]["slug"] == business.slug
    assert row["business"]["name"] == business.name


@pytest.mark.django_db
def test_never_includes_another_users_memberships(
    business_owner_client, second_business_owner, second_business
):
    """The actual isolation property: business_owner must never see
    second_business_owner's membership, even though both rows exist in
    the same table."""
    response = business_owner_client.get("/api/me/businesses/")
    assert response.status_code == 200
    slugs = [row["business"]["slug"] for row in response.data]
    assert second_business.slug not in slugs


@pytest.mark.django_db
def test_a_user_who_belongs_to_two_businesses_sees_both(business_owner_client, business_owner, second_business):
    BusinessMembership.objects.create(
        business=second_business, user=business_owner, role=BusinessMembership.Role.MANAGER
    )
    response = business_owner_client.get("/api/me/businesses/")
    assert response.status_code == 200
    slugs = {row["business"]["slug"] for row in response.data}
    assert slugs == {"genmars-store", second_business.slug}


@pytest.mark.django_db
def test_excludes_a_suspended_membership(business_owner_client, business_owner, second_business):
    BusinessMembership.objects.create(
        business=second_business,
        user=business_owner,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.MembershipStatus.SUSPENDED,
    )
    response = business_owner_client.get("/api/me/businesses/")
    assert response.status_code == 200
    slugs = [row["business"]["slug"] for row in response.data]
    assert second_business.slug not in slugs
