from rest_framework import permissions, viewsets

from core.permissions import IsStaffOrReadOnly

from .filters import ProductFilter
from .models import Category, Product, ProductImage, ProductVariant
from .serializers import (
    CategorySerializer,
    ProductImageSerializer,
    ProductSerializer,
    ProductVariantSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsStaffOrReadOnly]
    search_fields = ["name"]


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_class = ProductFilter
    search_fields = ["name", "sku"]
    ordering_fields = ["price", "created_at"]

    def get_queryset(self):
        qs = Product.objects.select_related("category").prefetch_related("images", "variants")
        user = self.request.user
        if user.is_authenticated and user.is_staff:
            return qs
        return qs.filter(status=Product.Status.PUBLISHED)


class ProductImageViewSet(viewsets.ModelViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    permission_classes = [permissions.IsAdminUser]


class ProductVariantViewSet(viewsets.ModelViewSet):
    queryset = ProductVariant.objects.all()
    serializer_class = ProductVariantSerializer
    permission_classes = [permissions.IsAdminUser]
