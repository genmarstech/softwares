"""
Phase 4B — health endpoint, request correlation, and the one new rate
limit (checkout) added in the staging-readiness review.
"""

import re

import pytest
from django.core.cache import cache


@pytest.mark.django_db
def test_health_check_returns_ok_and_nothing_else(api_client):
    response = api_client.get("/health/")
    assert response.status_code == 200
    # Exactly this shape — no config, no version info, no database name,
    # nothing beyond the one status field, ever.
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_check_requires_no_authentication(api_client):
    # No force_authenticate() anywhere in this test — an orchestrator or
    # load balancer polling this must never need credentials.
    response = api_client.get("/health/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_response_carries_a_generated_request_id_when_none_supplied(api_client):
    response = api_client.get("/health/")
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id)


@pytest.mark.django_db
def test_response_echoes_a_well_formed_supplied_request_id(api_client):
    response = api_client.get("/health/", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers.get("X-Request-ID") == "trace-abc-123"


@pytest.mark.django_db
def test_malformed_supplied_request_id_is_replaced_not_reflected(api_client):
    # Embedded whitespace/control-ish content shouldn't ride straight
    # through into a response header or a log line.
    response = api_client.get("/health/", headers={"X-Request-ID": "not a valid id\nwith a newline"})
    returned = response.headers.get("X-Request-ID")
    assert returned != "not a valid id\nwith a newline"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", returned)


@pytest.mark.django_db
def test_checkout_is_rate_limited(auth_client, cart_with_item):
    """
    checkout/ now has throttle_scope="checkout" (20/min, config/settings.py).
    Emptying the cart after the first real checkout would make every
    further attempt fail on "empty_cart" before ever reaching the
    throttle — so this only needs to prove the 21st *request* is throttled,
    not that 20 checkouts succeed; failed-but-not-throttled attempts still
    count against the scope the same as a successful one.
    """
    cache.clear()
    cart, product, record = cart_with_item
    delivery = {
        "delivery_recipient_name": "Ada Lovelace",
        "delivery_line1": "123 Moi Ave",
        "delivery_city": "Nairobi",
        "delivery_phone": "0712345678",
    }

    responses = [auth_client.post("/api/genmars-store/checkout/", delivery, format="json") for _ in range(21)]

    assert responses[0].status_code in (201, 400)  # first one succeeds or fails on business logic, not throttling
    assert responses[-1].status_code == 429
    cache.clear()
