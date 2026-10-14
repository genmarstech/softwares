from rest_framework import serializers

from .models import InventoryMovement, InventoryRecord


class InventoryRecordSerializer(serializers.ModelSerializer):
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryRecord
        fields = ["id", "product", "variant", "quantity", "low_stock_threshold", "is_low_stock"]
        read_only_fields = ["quantity"]  # only adjust_stock() may change quantity


class StockAdjustmentSerializer(serializers.Serializer):
    delta = serializers.IntegerField()
    reason = serializers.ChoiceField(choices=InventoryMovement.Reason.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("delta must not be zero.")
        return value


class InventoryMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryMovement
        fields = [
            "id", "record", "delta", "resulting_quantity",
            "reason", "reference", "note", "actor", "created_at",
        ]
        read_only_fields = fields
