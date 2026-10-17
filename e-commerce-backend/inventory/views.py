from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessManager, IsBusinessMember

from .models import InventoryMovement, InventoryRecord
from .serializers import (
    InventoryMovementSerializer,
    InventoryRecordSerializer,
    StockAdjustmentSerializer,
)
from .services import InsufficientStock, adjust_stock

# Creating/editing/deleting an InventoryRecord row (a new SKU's stock
# line) is catalog-management territory — owner/manager. Reading, and the
# two fulfilment actions below (adjust, movements), are any active
# member's — "staff: read products/inventory" and restocking/adjusting
# stock is squarely a staff/warehouse task, per the plan.
_MANAGER_ONLY_ACTIONS = {"create", "update", "partial_update", "destroy"}


class InventoryRecordViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    """
    `quantity` is read-only on the serializer — the only way to change it
    through this API is the `adjust` action, which always goes through
    `adjust_stock()` and leaves an `InventoryMovement` behind.
    """

    serializer_class = InventoryRecordSerializer
    filterset_fields = ["product", "variant"]
    search_fields = ["product__name", "product__sku", "variant__sku"]

    def get_permissions(self):
        permission_class = IsBusinessManager if self.action in _MANAGER_ONLY_ACTIONS else IsBusinessMember
        return [permission_class()]

    def get_queryset(self):
        return InventoryRecord.objects.filter(business=self.business).select_related("product", "variant")

    def perform_create(self, serializer):
        # product/variant-belongs-to-this-business is enforced in
        # InventoryRecordSerializer.validate() — shared with update.
        serializer.save(business=self.business)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None, business_slug=None):
        record = self.get_object()
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            record = adjust_stock(
                record,
                serializer.validated_data["delta"],
                reason=serializer.validated_data["reason"],
                actor=request.user,
                note=serializer.validated_data["note"],
            )
        except InsufficientStock as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response(InventoryRecordSerializer(record).data)

    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None, business_slug=None):
        record = self.get_object()
        movements = record.movements.all()[:200]
        return Response(InventoryMovementSerializer(movements, many=True).data)
