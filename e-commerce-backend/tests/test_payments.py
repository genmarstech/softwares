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


def _callback_body(checkout_request_id, *, success=True):
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "mr_1",
                "CheckoutRequestID": checkout_request_id,
                "ResultCode": 0 if success else 1,
                "ResultDesc": "Success" if success else "Cancelled by user",
                **(
                    {
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 1000},
                                {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                                {"Name": "PhoneNumber", "Value": 254712345678},
                            ]
                        }
                    }
                    if success
                    else {}
                ),
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
    response = api_client.post(
        "/api/payments/mpesa/callback/", _callback_body("ws_CO_TEST"), format="json"
    )
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

    first = api_client.post("/api/payments/mpesa/callback/", body, format="json")
    second = api_client.post("/api/payments/mpesa/callback/", body, format="json")

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
        "/api/payments/mpesa/callback/", _callback_body("ws_CO_TEST", success=False), format="json"
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.PAYMENT_FAILED


@pytest.mark.django_db
def test_callback_for_unknown_checkout_request_id_is_ignored_not_errored(api_client):
    response = api_client.post(
        "/api/payments/mpesa/callback/", _callback_body("not-a-real-id"), format="json"
    )
    assert response.status_code == 200
