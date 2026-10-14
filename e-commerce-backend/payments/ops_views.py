from rest_framework import mixins, permissions, viewsets

from .models import Payment
from .serializers import PaymentSerializer


class OpsPaymentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only — a Payment row is only ever written by the webhook (see
    payments/views.py::MpesaCallbackView). Staff can look, never edit."""

    queryset = Payment.objects.select_related("order")
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAdminUser]
    filterset_fields = ["status", "provider"]
    search_fields = ["order__reference", "receipt_number", "phone_number"]
    ordering_fields = ["created_at", "amount"]
