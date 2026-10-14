from decimal import Decimal

from rest_framework import serializers

from catalog.models import Product

from .models import Cart, CartItem


class CartItemSerializer(serializers.ModelSerializer):
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = CartItem
        fields = ["id", "product", "variant", "product_name", "quantity", "unit_price"]

    def validate(self, attrs):
        product = attrs.get("product") or getattr(self.instance, "product", None)
        variant = attrs.get("variant")
        if variant and variant.product_id != product.id:
            raise serializers.ValidationError("That variant does not belong to this product.")
        if product and product.status != Product.Status.PUBLISHED:
            raise serializers.ValidationError("This product is not available.")
        return attrs

    def validate_quantity(self, value):
        if value < 1:
            raise serializers.ValidationError("Quantity must be at least 1.")
        return value


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ["id", "items", "subtotal", "updated_at"]

    def get_subtotal(self, obj) -> Decimal:
        return sum((item.unit_price * item.quantity for item in obj.items.all()), start=Decimal(0))
