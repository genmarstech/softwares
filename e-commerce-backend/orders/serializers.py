from rest_framework import serializers

from .models import Order, OrderItem, OrderStatusHistory


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_name", "sku", "unit_price", "quantity", "line_total"]
        read_only_fields = fields


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ["id", "from_status", "to_status", "note", "actor", "created_at"]
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "reference", "status", "currency",
            "subtotal", "delivery_fee", "discount_total", "tax_total", "total",
            "contact_email", "contact_phone",
            "delivery_recipient_name", "delivery_line1", "delivery_line2",
            "delivery_city", "delivery_phone",
            "items", "status_history", "created_at", "updated_at",
        ]
        read_only_fields = fields


class CheckoutSerializer(serializers.Serializer):
    """
    Either `address_id` (an existing accounts.Address) or the delivery
    fields directly. The view resolves whichever was given into the
    concrete fields orders.services.create_order_from_cart() needs.
    """

    address_id = serializers.IntegerField(required=False)

    delivery_recipient_name = serializers.CharField(required=False, max_length=150)
    delivery_line1 = serializers.CharField(required=False, max_length=255)
    delivery_line2 = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    delivery_city = serializers.CharField(required=False, max_length=100)
    delivery_phone = serializers.CharField(required=False, max_length=20)
    contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=20, default="")

    def validate(self, attrs):
        has_address_id = "address_id" in attrs
        has_manual_fields = all(
            attrs.get(f)
            for f in ["delivery_recipient_name", "delivery_line1", "delivery_city", "delivery_phone"]
        )
        if not has_address_id and not has_manual_fields:
            raise serializers.ValidationError(
                "Provide either address_id or delivery_recipient_name/line1/city/phone."
            )
        return attrs
