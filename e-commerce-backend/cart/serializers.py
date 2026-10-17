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
        variant = attrs.get("variant") if "variant" in attrs else getattr(self.instance, "variant", None)
        if variant and variant.product_id != product.id:
            raise serializers.ValidationError("That variant does not belong to this product.")
        if product and product.status != Product.Status.PUBLISHED:
            raise serializers.ValidationError("This product is not available.")

        # Must run on EVERY write, not just create — a PATCH/PUT can change
        # `product`/`variant` on an existing item too, and the item's
        # queryset scope (cart__business=self.business) only proves the
        # CART being written to belongs to this business, never that the
        # product being attached to it does. Checked here, once, rather than
        # in perform_create() alone, so update can't bypass what create
        # enforces — a customer could otherwise PATCH an existing genmars-
        # store cart item's `product` to another business's id entirely and
        # have it stick (confirmed live before this fix: the swapped item
        # survived to a real Order and decremented the OTHER business's
        # stock).
        business = self.context.get("business")
        owner = variant.product if variant else product
        if owner and business and owner.business_id != business.pk:
            raise serializers.ValidationError({"product": "That product is not sold by this business."})
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
