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

# cart_with_item is 2 x a KES 500.00 product, no delivery fee by default —
# the order total (and so the expected callback Amount) is always 1000.
ORDER_TOTAL = 1000

CALLBACK_SECRET = "test-only-callback-secret"
CALLBACK_URL = f"/api/payments/mpesa/callback/{CALLBACK_SECRET}/"

# Sentinel distinct from None — "the test deliberately omitted the Amount
# item" rather than "the test didn't think about it."
_OMIT = object()


@pytest.fixture(autouse=True)
def _mpesa_callback_secret(settings):
    settings.MPESA_CALLBACK_SECRET = CALLBACK_SECRET


def _callback_body(checkout_request_id, *, success=True, amount=ORDER_TOTAL):
    items = []
    if success:
        if amount is not _OMIT:
            items.append({"Name": "Amount", "Value": amount})
        items.append({"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"})
        items.append({"Name": "PhoneNumber", "Value": 254712345678})

    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "mr_1",
                "CheckoutRequestID": checkout_request_id,
                "ResultCode": 0 if success else 1,
                "ResultDesc": "Success" if success else "Cancelled by user",
                **({"CallbackMetadata": {"Item": items}} if success else {}),
            }
        }
    }


@pytest.fixture
def pending_payment(customer, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(customer, cart, **DELIVERY)
    payment = Payment.objects.create(
        order=order,
        amount=order.total,
        currency=order.currency,
        phone_number="254712345678",
        checkout_request_id="ws_CO_TEST",
        merchant_request_id="mr_1",
    )
    return order, payment


@pytest.mark.django_db
def test_successful_callback_marks_payment_and_order_paid(api_client, pending_payment):
    order, payment = pending_payment
    response = api_client.post(CALLBACK_URL, _callback_body("ws_CO_TEST"), format="json")
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.SUCCESSFUL
    assert payment.receipt_number == "NLJ7RT61SV"
    assert order.status == Order.Status.PAID


@pytest.mark.django_db
def test_duplicate_callback_delivery_is_a_no_op(api_client, pending_payment):
    order, payment = pending_payment
    body = _callback_body("ws_CO_TEST")

    first = api_client.post(CALLBACK_URL, body, format="json")
    second = api_client.post(CALLBACK_URL, body, format="json")

    assert first.status_code == 200
    assert second.status_code == 200

    order.refresh_from_db()
    assert order.status == Order.Status.PAID
    # Exactly one PAID transition recorded — the duplicate changed nothing.
    paid_history = order.status_history.filter(to_status=Order.Status.PAID)
    assert paid_history.count() == 1


@pytest.mark.django_db
def test_failed_callback_marks_payment_failed_and_order_retriable(api_client, pending_payment):
    order, payment = pending_payment
    response = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", success=False), format="json"
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.PAYMENT_FAILED


@pytest.mark.django_db
def test_callback_for_unknown_checkout_request_id_is_ignored_not_errored(api_client):
    response = api_client.post(CALLBACK_URL, _callback_body("not-a-real-id"), format="json")
    assert response.status_code == 200


# ── Callback authenticity ───────────────────────────────────────────────


@pytest.mark.django_db
def test_callback_with_wrong_token_is_rejected_and_payment_untouched(api_client, pending_payment):
    order, payment = pending_payment
    wrong_url = "/api/payments/mpesa/callback/not-the-real-secret/"
    response = api_client.post(wrong_url, _callback_body("ws_CO_TEST"), format="json")
    assert response.status_code == 403

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.processed_at is None
    assert order.status == Order.Status.AWAITING_PAYMENT


@pytest.mark.django_db
def test_callback_rejected_when_no_secret_is_configured(api_client, pending_payment, settings):
    order, payment = pending_payment
    settings.MPESA_CALLBACK_SECRET = ""  # unconfigured must mean closed, not open
    response = api_client.post(
        "/api/payments/mpesa/callback/anything/", _callback_body("ws_CO_TEST"), format="json"
    )
    assert response.status_code == 403

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ── Callback amount validation ──────────────────────────────────────────


@pytest.mark.django_db
def test_callback_amount_mismatch_marks_payment_failed_not_paid(api_client, pending_payment):
    order, payment = pending_payment
    response = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", amount=ORDER_TOTAL - 500), format="json"
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.receipt_number == ""
    assert order.status == Order.Status.PAYMENT_FAILED


@pytest.mark.django_db
def test_callback_missing_amount_marks_payment_failed_not_paid(api_client, pending_payment):
    order, payment = pending_payment
    response = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", amount=_OMIT), format="json"
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.PAYMENT_FAILED


@pytest.mark.django_db
def test_callback_malformed_amount_marks_payment_failed_not_paid(api_client, pending_payment):
    order, payment = pending_payment
    response = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", amount="not-a-number"), format="json"
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.PAYMENT_FAILED


@pytest.mark.django_db
def test_conflicting_duplicate_callback_cannot_overturn_a_finalized_failure(api_client, pending_payment):
    """
    First delivery reports success with a tampered/wrong amount — correctly
    recorded as failed. A second delivery for the SAME CheckoutRequestID
    then reports success with the correct amount. This must not be allowed
    to flip an already-finalized payment: duplicate-delivery idempotency
    applies regardless of whether the retry's claim happens to agree with
    the first one or conflicts with it.
    """
    order, payment = pending_payment

    first = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", amount=ORDER_TOTAL - 500), format="json"
    )
    assert first.status_code == 200
    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED

    second = api_client.post(
        CALLBACK_URL, _callback_body("ws_CO_TEST", amount=ORDER_TOTAL), format="json"
    )
    assert second.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.receipt_number == ""
    assert order.status == Order.Status.PAYMENT_FAILED
