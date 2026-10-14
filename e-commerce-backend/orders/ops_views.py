"""
Staff view of every order in the company — not scoped to the caller. This is
the `/api/ops/` counterpart to `orders/views.py::OrderViewSet`, which only
ever returns the requesting customer's own orders. Kept in a separate module
so a reviewer scanning `views.py` for "what can a customer do" never has to
mentally filter out the staff-only surface.
"""

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessStaff

from .models import Order
from .serializers import OrderSerializer, OrderSummarySerializer
from .services import OrderError
from .services import transition as transition_order


class OpsOrderViewSet(BusinessScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsBusinessStaff]
    filterset_fields = ["status"]
    search_fields = ["reference", "customer__email", "contact_email", "contact_phone"]
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        return (
            Order.objects.filter(business=self.business)
            .select_related("customer")
            .prefetch_related("items", "status_history")
        )

    def get_serializer_class(self):
        return OrderSerializer if self.action == "retrieve" else OrderSummarySerializer

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None, business_slug=None):
        """
        The same `orders.services.transition()` the Django admin actions
        already call — one state machine, two front doors onto it.
        """
        order = self.get_object()
        to_status = request.data.get("to_status")
        if not to_status:
            return Response({"detail": "to_status is required."}, status=400)

        try:
            order = transition_order(order, to_status, actor=request.user, note=request.data.get("note", ""))
        except OrderError as exc:
            return Response({"detail": exc.safe_message}, status=400)

        return Response(OrderSerializer(order).data)
