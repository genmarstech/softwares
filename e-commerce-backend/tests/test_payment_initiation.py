"""
Phase 6 audit: InitiatePaymentView had exactly one test anywhere in this
suite before this file (a cross-business 404 check in
test_tenant_isolation.py) — nothing proved the disabled-M-Pesa safe
rejection, the order-must-be-payable guard, or the "a successful STK push
only ever creates a PENDING Payment row, never marks the order paid"
property that the whole payment-safety story depends on. `unittest.mock`
stands in for Daraja here — this project's own standing rule is that no
real STK push has ever been fired and firing one is the user's call, not
a test's; see payments/mpesa.py and CLAUDE.md.
"""

from unittest.mock import patch

import pytest

from orders.models import Order
from orders.services import create_order_from_cart
from payments.models import Payment

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)

INITIATE_BODY = {"order_reference": None, "phone": "0712345678"}


@pytest.fixture
def awaiting_order(customer, cart_with_item):
    cart, product, record = cart_with_item
    return create_order_from_cart(customer, cart, **DELIVERY)


@pytest.mark.django_db
def test_initiate_with_mpesa_disabled_is_a_safe_rejection_not_a_crash(api_client, awaiting_order):
    """MPESA_ENABLED=False is this project's own default — mpesa.stk_push()
    raises before any network call; the view must turn that into a clean
    4xx with no Payment row created, never a 500."""
    api_client.force_authenticate(user=awaiting_order.customer)
    response = api_client.post(
        "/api/genmars-store/payments/mpesa/initiate/",
        {"order_reference": awaiting_order.reference, "phone": "0712345678"},
        format="json",
    )
    assert response.status_code == 502
    assert "detail" in response.data
    assert not Payment.objects.filter(order=awaiting_order).exists()
    awaiting_order.refresh_from_db()
    assert awaiting_order.status == Order.Status.AWAITING_PAYMENT


@pytest.mark.django_db
def test_initiate_rejects_an_order_that_is_not_awaiting_payment(api_client, awaiting_order, settings):
    """PAID (or any other non-payable status) must refuse before even
    trying to reach Daraja — mirrors ORDER_PAYABLE_STATUSES."""
    awaiting_order.status = Order.Status.PAID
    awaiting_order.save(update_fields=["status"])

    api_client.force_authenticate(user=awaiting_order.customer)
    with patch("payments.views.mpesa.stk_push") as mock_stk_push:
        response = api_client.post(
            "/api/genmars-store/payments/mpesa/initiate/",
            {"order_reference": awaiting_order.reference, "phone": "0712345678"},
            format="json",
        )
    assert response.status_code == 400
    mock_stk_push.assert_not_called()
    assert not Payment.objects.filter(order=awaiting_order).exists()


@pytest.mark.django_db
def test_successful_stk_push_creates_only_a_pending_payment_never_marks_the_order_paid(
    api_client, awaiting_order, settings
):
    """
    The core payment-safety property: asking Safaricom to prompt a phone is
    not the same as the customer having paid. Even a successful-looking STK
    response (ResponseCode "0" — "we sent the prompt") must leave the order
    exactly as it was; only the callback (payments/views.py::
    MpesaCallbackView, covered in test_payments.py) is allowed to move it
    to PAID.
    """
    settings.MPESA_ENABLED = True
    api_client.force_authenticate(user=awaiting_order.customer)

    with patch("payments.views.mpesa.stk_push") as mock_stk_push:
        mock_stk_push.return_value = {
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "MerchantRequestID": "mocked-merchant-request-1",
            "CheckoutRequestID": "mocked-checkout-request-1",
        }
        response = api_client.post(
            "/api/genmars-store/payments/mpesa/initiate/",
            {"order_reference": awaiting_order.reference, "phone": "0712345678"},
            format="json",
        )

    assert response.status_code == 202
    mock_stk_push.assert_called_once()

    payment = Payment.objects.get(order=awaiting_order)
    assert payment.status == Payment.Status.PENDING
    assert payment.checkout_request_id == "mocked-checkout-request-1"
    assert payment.amount == awaiting_order.total

    awaiting_order.refresh_from_db()
    assert awaiting_order.status == Order.Status.AWAITING_PAYMENT  # unchanged — only the callback moves this


@pytest.mark.django_db
def test_a_failed_stk_push_response_is_surfaced_and_creates_no_payment_row(api_client, awaiting_order, settings):
    """Daraja can accept the HTTP call but refuse the push itself
    (ResponseCode != "0") — mpesa.stk_push() raises MpesaError for that;
    the view must not create a Payment row for a push that never went out."""
    settings.MPESA_ENABLED = True
    api_client.force_authenticate(user=awaiting_order.customer)

    from payments import mpesa

    with patch("payments.views.mpesa.stk_push", side_effect=mpesa.MpesaError("M-Pesa refused the request.")):
        response = api_client.post(
            "/api/genmars-store/payments/mpesa/initiate/",
            {"order_reference": awaiting_order.reference, "phone": "0712345678"},
            format="json",
        )

    assert response.status_code == 502
    assert not Payment.objects.filter(order=awaiting_order).exists()


@pytest.mark.django_db
def test_initiate_rejects_an_invalid_phone_number_before_calling_mpesa(api_client, awaiting_order):
    api_client.force_authenticate(user=awaiting_order.customer)
    with patch("payments.views.mpesa.stk_push") as mock_stk_push:
        response = api_client.post(
            "/api/genmars-store/payments/mpesa/initiate/",
            {"order_reference": awaiting_order.reference, "phone": "not-a-phone-number"},
            format="json",
        )
    assert response.status_code == 400
    mock_stk_push.assert_not_called()
