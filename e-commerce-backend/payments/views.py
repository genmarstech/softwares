import logging
from decimal import ROUND_HALF_UP

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Order
from orders.services import OrderError, transition

from . import mpesa
from .models import Payment
from .serializers import InitiatePaymentSerializer, PaymentSerializer

log = logging.getLogger(__name__)

ORDER_PAYABLE_STATUSES = {Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED}


class InitiatePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "payment-initiate"
    serializer_class = InitiatePaymentSerializer

    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 404, not 403: a reference that exists but isn't this customer's
        # must look identical to one that doesn't exist at all.
        order = get_object_or_404(
            Order, reference=serializer.validated_data["order_reference"], customer=request.user
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
    stock-restore or a second "payment confirmed" email.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: None})
    @transaction.atomic
    def post(self, request):
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
        if parsed["result_code"] == "0":
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
            try:
                transition(payment.order, Order.Status.PAYMENT_FAILED, actor=None, note=parsed["result_desc"])
            except OrderError:
                log.info("could not move %s to payment_failed", payment.order.reference)

        return Response(status=status.HTTP_200_OK)
