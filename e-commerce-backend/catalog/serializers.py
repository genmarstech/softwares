from django.conf import settings
from django.core.files.images import get_image_dimensions
from rest_framework import serializers

from .models import Category, Product, ProductImage, ProductVariant


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent"]
        read_only_fields = ["slug"]

    def validate_name(self, value):
        # Uniqueness is per-business, not global (catalog/models.py), and
        # the model field is no longer `unique=True` — so DRF stopped
        # generating a UniqueValidator for it automatically. This replaces
        # that, scoped correctly, so a clash is still a 400 with a clear
        # message rather than a raw IntegrityError surfacing as a 500.
        business = self.context.get("business")
        qs = Category.objects.filter(business=business, name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if business and qs.exists():
            raise serializers.ValidationError("A category with this name already exists for this business.")
        return value


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "product", "image", "sort_order"]

    def validate_product(self, value):
        # Must run on update as well as create — ProductImageViewSet's own
        # perform_create() checks this for a new row, but nothing stopped a
        # PATCH to an EXISTING image (one already scoped to this business via
        # get_queryset()) from reassigning its `product` straight onto
        # another business's product instead. Same class of bug as
        # cart/serializers.py's CartItemSerializer.validate() fix — checked
        # here so both create and update share one enforcement point.
        business = self.context.get("business")
        if business and value.business_id != business.pk:
            raise serializers.ValidationError("That product does not belong to this business.")
        return value

    def validate_image(self, value):
        # DRF's ImageField has already made Pillow open this and confirmed
        # it's a genuine image by the time this runs — content validation
        # by file signature, not just extension/content-type header, which
        # a renamed arbitrary file can't fake its way past. This adds the
        # two checks that alone doesn't cover: how big, and how large a
        # canvas.
        max_bytes = settings.PRODUCT_IMAGE_MAX_BYTES
        if value.size > max_bytes:
            raise serializers.ValidationError(
                f"Image must be smaller than {max_bytes // (1024 * 1024)}MB."
            )

        max_dimension = settings.PRODUCT_IMAGE_MAX_DIMENSION_PX
        width, height = get_image_dimensions(value)
        if width is None or height is None:
            raise serializers.ValidationError("Could not read this image's dimensions.")
        if width > max_dimension or height > max_dimension:
            raise serializers.ValidationError(
                f"Image dimensions must not exceed {max_dimension}x{max_dimension}px."
            )
        return value


class ProductVariantSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "product", "name", "value", "sku", "price_override", "price"]

    def validate_product(self, value):
        # Same reasoning as ProductImageSerializer.validate_product above —
        # ProductVariantViewSet.perform_create() only checks this for a new
        # row; without this, a PATCH to an existing variant could reassign
        # it onto another business's product.
        business = self.context.get("business")
        if business and value.business_id != business.pk:
            raise serializers.ValidationError("That product does not belong to this business.")
        return value

    def validate_sku(self, value):
        # Same reasoning as Category/Product above — a variant's sku is
        # unique per-business (catalog/models.py), not globally.
        business = self.context.get("business")
        qs = ProductVariant.objects.filter(business=business, sku=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if business and qs.exists():
            raise serializers.ValidationError("A variant with this SKU already exists for this business.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "name", "slug", "description", "sku", "price", "status",
            "category", "images", "variants", "created_at", "updated_at",
        ]
        read_only_fields = ["slug", "created_at", "updated_at"]

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than zero.")
        return value

    def validate_sku(self, value):
        # Same reasoning as CategorySerializer.validate_name above — sku's
        # uniqueness is per-business now, so the auto-generated
        # UniqueValidator that came with the old `unique=True` is gone.
        business = self.context.get("business")
        qs = Product.objects.filter(business=business, sku=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if business and qs.exists():
            raise serializers.ValidationError("A product with this SKU already exists for this business.")
        return value
