import logging
import secrets
from decimal import ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.mixins import BusinessScopedMixin
from orders.models import Order
from orders.services import OrderError, transition

from . import mpesa
from .models import Payment
from .serializers import InitiatePaymentSerializer, PaymentSerializer

log = logging.getLogger(__name__)

ORDER_PAYABLE_STATUSES = {Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED}


class InitiatePaymentView(BusinessScopedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "payment-initiate"
    serializer_class = InitiatePaymentSerializer

    def post(self, request, business_slug=None):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 404, not 403: a reference that exists but isn't this customer's,
        # or isn't this business's, must look identical to one that doesn't
        # exist at all — a customer who has ordered from two businesses
        # must not be able to pay for business A's order through business
        # B's URL just because both orders happen to be theirs.
        order = get_object_or_404(
            Order,
            reference=serializer.validated_data["order_reference"],
            customer=request.user,
            business=self.business,
        )
        if order.status not in ORDER_PAYABLE_STATUSES:
            return Response({"detail": "This order is not awaiting payment."}, status=400)

        try:
            phone = mpesa.normalise_phone(serializer.validated_data["phone"])
        except mpesa.MpesaError as exc:
            return Response({"detail": str(exc)}, status=400)

        amount = int(order.total.to_integral_value(rounding=ROUND_HALF_UP))

        try:
            response = mpesa.stk_push(
                phone=phone, amount=amount, reference=order.reference, description="Order payment"
            )
        except mpesa.MpesaError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        payment = Payment.objects.create(
            order=order,
            amount=order.total,
            currency=order.currency,
            phone_number=phone,
            merchant_request_id=response.get("MerchantRequestID", ""),
            checkout_request_id=response.get("CheckoutRequestID"),
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_202_ACCEPTED)


class MpesaCallbackView(APIView):
    """
    Safaricom calls this server-to-server — no DRF auth, and nothing here
    should ever be reachable from a browser redirect. Idempotent: a
    `Payment` already resolved (`processed_at` set) is a no-op 200, which is
    what makes a duplicate callback delivery harmless rather than a double
    stock-restore or a second "payment confirmed" email — and that guard
    runs before the success/failure branch below, so a *conflicting* retry
    (a second delivery disagreeing with the first about the outcome) can
    never overwrite an already-finalized payment either.

    ── Authenticity ─────────────────────────────────────────────────────
    Daraja does not sign this request — there is no header or body field
    that proves Safaricom sent it, and the URL's obscurity alone is not a
    control. `token` (the final URL segment) is checked against
    MPESA_CALLBACK_SECRET, a value only this server and the Daraja app
    configuration know; a missing or wrong token is rejected before
    anything else runs, and an unconfigured secret rejects everything
    rather than accepting anything. This is a documented limitation of
    Daraja, not a gap in this view: a production deployment should also
    restrict the route at the network layer (Safaricom's published
    callback IP ranges) as defence in depth, which is outside what Django
    alone can enforce.

    ── Amount ───────────────────────────────────────────────────────────
    A ResultCode of 0 means Safaricom is claiming success, not that the
    claim is trustworthy. The claimed Amount must equal what this payment
    was actually raised for (`Payment.amount`, rounded the same way the
    STK push itself rounded it) before this view will move anything to
    PAID — missing, non-numeric, or merely different all fail the same
    check and fall into the same "failed" branch a declined prompt does.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: None, 403: None})
    @transaction.atomic
    def post(self, request, token):
        if not settings.MPESA_CALLBACK_SECRET or not secrets.compare_digest(
            token, settings.MPESA_CALLBACK_SECRET
        ):
            log.warning("mpesa callback rejected: callback token did not match")
            return Response(status=status.HTTP_403_FORBIDDEN)

        parsed = mpesa.parse_callback(request.data)
        checkout_request_id = parsed["checkout_request_id"]
        if not checkout_request_id:
            log.warning("mpesa callback with no CheckoutRequestID: %s", request.data)
            return Response(status=status.HTTP_200_OK)

        payment = (
            Payment.objects.select_for_update()
            .filter(checkout_request_id=checkout_request_id)
            .first()
        )
        if payment is None:
            log.warning("mpesa callback for unknown CheckoutRequestID %s", checkout_request_id)
            return Response(status=status.HTTP_200_OK)

        if payment.processed_at is not None:
            return Response(status=status.HTTP_200_OK)  # already resolved — duplicate delivery

        payment.raw_callback = request.data
        payment.processed_at = timezone.now()

        # The Payment row itself is always saved first and unconditionally —
        # that's the durable record of what Safaricom told us. Moving the
        # Order's status is a best-effort follow-on: if it can't happen (the
        # order was, say, already cancelled by admin while the prompt was in
        # flight) that's a mismatch for a human to reconcile, not a reason to
        # 500 and make Safaricom retry a payment we've already recorded.
        expected_amount = int(payment.amount.to_integral_value(rounding=ROUND_HALF_UP))
        reported_amount = mpesa.coerce_amount(parsed["amount"]) if parsed["result_code"] == "0" else None
        claims_success = parsed["result_code"] == "0"
        amount_checks_out = claims_success and reported_amount == expected_amount

        if claims_success and not amount_checks_out:
            log.error(
                "mpesa callback for order %s claimed success but the amount did not "
                "check out (expected %s, got %r) — recording as failed, not paid",
                payment.order.reference, expected_amount, parsed["amount"],
            )

        if amount_checks_out:
            payment.status = Payment.Status.SUCCESSFUL
            payment.receipt_number = parsed["receipt"]
            payment.save(update_fields=["status", "receipt_number", "raw_callback", "processed_at"])
            try:
                transition(
                    payment.order,
                    Order.Status.PAID,
                    actor=None,
                    note=f"M-Pesa receipt {payment.receipt_number}",
                )
            except OrderError:
                log.warning(
                    "payment %s succeeded but order %s could not move to paid (status=%s)",
                    payment.pk, payment.order.reference, payment.order.status,
                )
        else:
            payment.status = Payment.Status.FAILED
            payment.save(update_fields=["status", "raw_callback", "processed_at"])
            note = parsed["result_desc"] if not claims_success else "Payment could not be verified."
            try:
                transition(payment.order, Order.Status.PAYMENT_FAILED, actor=None, note=note)
            except OrderError:
                log.info("could not move %s to payment_failed", payment.order.reference)

        return Response(status=status.HTTP_200_OK)
