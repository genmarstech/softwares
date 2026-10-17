from rest_framework import viewsets
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response

from analytics.models import StorefrontEvent
from analytics.services import record as record_event
from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessManager, IsBusinessManagerOrReadOnly, IsBusinessMember, caller_role

from .filters import ProductFilter
from .models import Category, Product, ProductImage, ProductVariant
from .serializers import (
    CategorySerializer,
    ProductImageSerializer,
    ProductSerializer,
    ProductVariantSerializer,
)


class CategoryViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    # Public read; managing the catalog is owner/manager, not staff — see
    # businesses/permissions.py's module docstring for the role split.
    permission_classes = [IsBusinessManagerOrReadOnly]
    search_fields = ["name"]

    def get_queryset(self):
        return Category.objects.filter(business=self.business)

    def perform_create(self, serializer):
        serializer.save(business=self.business)


class ProductViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsBusinessManagerOrReadOnly]
    filterset_class = ProductFilter
    search_fields = ["name", "sku"]
    ordering_fields = ["price", "created_at"]

    def get_queryset(self):
        qs = Product.objects.filter(business=self.business).select_related("category").prefetch_related(
            "images", "variants"
        )
        # A draft/archived product is visible to this business's own
        # members, any role (staff included — "staff: read products") —
        # scoped to membership in *this* business instead of the platform.
        if caller_role(self.request, self) is not None:
            return qs
        return qs.filter(status=Product.Status.PUBLISHED)

    def perform_create(self, serializer):
        serializer.save(business=self.business)

    def list(self, request, *args, **kwargs):
        # One storefront "visit" per product-list request — see
        # analytics/models.py. Logged after super().list() succeeds so a
        # pagination/filter error never gets counted as a visit.
        response = super().list(request, *args, **kwargs)
        record_event(self.business, StorefrontEvent.EventType.VISIT)
        return response

    def retrieve(self, request, *args, **kwargs):
        # Mirrors DRF's own RetrieveModelMixin.retrieve() rather than
        # calling super() and then self.get_object() again, which would
        # fetch the same row from the database twice for every single
        # product page view.
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        record_event(self.business, StorefrontEvent.EventType.PRODUCT_VIEW, product=instance)
        return Response(serializer.data)


class ProductImageViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = ProductImageSerializer

    def get_permissions(self):
        # No public-read use case for this standalone management endpoint
        # (a storefront visitor sees images via the parent product's own
        # serialized data) — but staff can still look, same as the catalog
        # generally; only owner/manager can attach or remove one.
        permission_class = IsBusinessMember if self.request.method in SAFE_METHODS else IsBusinessManager
        return [permission_class()]

    def get_queryset(self):
        return ProductImage.objects.filter(product__business=self.business)

    # product-belongs-to-this-business is enforced in
    # ProductImageSerializer.validate_product() — shared with update.


class ProductVariantViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = ProductVariantSerializer

    def get_permissions(self):
        permission_class = IsBusinessMember if self.request.method in SAFE_METHODS else IsBusinessManager
        return [permission_class()]

    def get_queryset(self):
        return ProductVariant.objects.filter(product__business=self.business)

    def perform_create(self, serializer):
        # product-belongs-to-this-business is enforced in
        # ProductVariantSerializer.validate_product() — shared with update.
        # This still needs to inject `business` itself: a variant has its
        # own denormalized `business` FK (catalog/models.py), which nothing
        # else sets on create.
        serializer.save(business=serializer.validated_data["product"].business)
