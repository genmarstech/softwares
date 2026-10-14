from rest_framework import mixins, viewsets

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessStaff

from .models import Payment
from .serializers import PaymentSerializer


class OpsPaymentViewSet(BusinessScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only — a Payment row is only ever written by the webhook (see
    payments/views.py::MpesaCallbackView). This business's own staff can
    look, never edit."""

    serializer_class = PaymentSerializer
    permission_classes = [IsBusinessStaff]
    filterset_fields = ["status", "provider"]
    search_fields = ["order__reference", "receipt_number", "phone_number"]
    ordering_fields = ["created_at", "amount"]

    def get_queryset(self):
        return Payment.objects.filter(order__business=self.business).select_related("order")
