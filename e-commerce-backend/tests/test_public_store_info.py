"""
GET /api/<slug>/store/ — the public, anonymous-readable counterpart to
StoreSettingsView. Exists so a storefront visitor (and the frontend's
build/request-time theming) can get a business's real display name,
currency and theme without any of Business's other fields, or StoreSettings'
own delivery-fee/threshold fields, leaking out alongside them.
"""

from core.models import StoreSettings


def test_anonymous_request_sees_name_currency_and_theme(api_client, business):
    StoreSettings.objects.update_or_create(
        business=business, defaults={"currency": "KES", "theme": StoreSettings.Theme.FASHION}
    )

    response = api_client.get(f"/api/{business.slug}/store/")

    assert response.status_code == 200
    assert response.data == {
        "name": business.name,
        "currency": "KES",
        "theme": "fashion",
    }


def test_defaults_to_the_default_theme_when_never_set(api_client, business):
    response = api_client.get(f"/api/{business.slug}/store/")

    assert response.status_code == 200
    assert response.data["theme"] == "default"


def test_unknown_slug_404s_same_as_every_other_public_endpoint(api_client, db):
    response = api_client.get("/api/no-such-business/store/")
    assert response.status_code == 404


def test_suspended_business_404s_not_200_with_stale_data(api_client, business):
    business.status = business.Status.SUSPENDED
    business.save(update_fields=["status"])

    response = api_client.get(f"/api/{business.slug}/store/")
    assert response.status_code == 404


def test_no_other_business_fields_leak_through(api_client, business):
    response = api_client.get(f"/api/{business.slug}/store/")
    assert set(response.data.keys()) == {"name", "currency", "theme"}


def test_each_business_sees_only_its_own_theme(api_client, business, second_business):
    StoreSettings.objects.update_or_create(business=business, defaults={"theme": StoreSettings.Theme.FASHION})
    StoreSettings.objects.update_or_create(
        business=second_business, defaults={"theme": StoreSettings.Theme.ELECTRONICS}
    )

    assert api_client.get(f"/api/{business.slug}/store/").data["theme"] == "fashion"
    assert api_client.get(f"/api/{second_business.slug}/store/").data["theme"] == "electronics"


def test_write_methods_are_not_exposed_here_at_all(api_client, business):
    # Changing currency/theme stays behind StoreSettingsView's
    # IsBusinessManager — this endpoint defines no patch()/post() at all,
    # so a write attempt 405s before any permission check runs.
    response = api_client.patch(f"/api/{business.slug}/store/", {"theme": "fashion"}, format="json")
    assert response.status_code == 405
