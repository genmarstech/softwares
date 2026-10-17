from rest_framework import serializers

from .models import InventoryMovement, InventoryRecord


class InventoryRecordSerializer(serializers.ModelSerializer):
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryRecord
        fields = ["id", "product", "variant", "quantity", "low_stock_threshold", "is_low_stock"]
        read_only_fields = ["quantity"]  # only adjust_stock() may change quantity

    def validate(self, attrs):
        # Must run on update as well as create — InventoryRecordViewSet's
        # own perform_create() checked this for a new row, but a PATCH to an
        # existing record (one already scoped to this business via
        # get_queryset()) could otherwise reassign its `product`/`variant`
        # straight onto another business's product, hijacking that other
        # business's stock tracking under this business's record. Same class
        # of bug as cart/serializers.py's CartItemSerializer.validate().
        product = attrs.get("product") or getattr(self.instance, "product", None)
        variant = attrs.get("variant") if "variant" in attrs else getattr(self.instance, "variant", None)
        business = self.context.get("business")
        owner = variant.product if variant else product
        if business and owner is not None and owner.business_id != business.pk:
            raise serializers.ValidationError({"product": "That product does not belong to this business."})
        return attrs


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
