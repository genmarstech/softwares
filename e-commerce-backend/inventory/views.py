from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import InventoryMovement, InventoryRecord
from .serializers import (
    InventoryMovementSerializer,
    InventoryRecordSerializer,
    StockAdjustmentSerializer,
)
from .services import InsufficientStock, adjust_stock


class InventoryRecordViewSet(viewsets.ModelViewSet):
    """
    Staff-only. `quantity` is read-only on the serializer — the only way to
    change it through this API is the `adjust` action, which always goes
    through `adjust_stock()` and leaves an `InventoryMovement` behind.
    """

    queryset = InventoryRecord.objects.select_related("product", "variant")
    serializer_class = InventoryRecordSerializer
    permission_classes = [permissions.IsAdminUser]

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
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
    def movements(self, request, pk=None):
        record = self.get_object()
        movements = record.movements.all()[:200]
        return Response(InventoryMovementSerializer(movements, many=True).data)
