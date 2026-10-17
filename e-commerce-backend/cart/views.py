from rest_framework import permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.models import StorefrontEvent
from analytics.services import record as record_event
from businesses.mixins import BusinessScopedMixin

from .models import Cart, CartItem
from .serializers import CartItemSerializer, CartSerializer


class CartView(BusinessScopedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CartSerializer

    def get(self, request, business_slug=None):
        cart, _ = Cart.objects.get_or_create(user=request.user, business=self.business)
        return Response(CartSerializer(cart).data)


class CartItemViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()
        return CartItem.objects.filter(
            cart__user=self.request.user, cart__business=self.business
        ).select_related("product", "variant")

    def perform_create(self, serializer):
        # The product/variant-belongs-to-this-business check lives in
        # CartItemSerializer.validate() — shared with update/partial_update,
        # so it can't be bypassed by PATCHing an existing item's product
        # instead of creating a new one.
        cart, _ = Cart.objects.get_or_create(user=self.request.user, business=self.business)
        product = serializer.validated_data["product"]
        variant = serializer.validated_data.get("variant")

        existing = CartItem.objects.filter(cart=cart, product=product, variant=variant).first()
        if existing:
            existing.quantity += serializer.validated_data["quantity"]
            existing.save(update_fields=["quantity"])
            serializer.instance = existing
        else:
            serializer.save(cart=cart)

        record_event(self.business, StorefrontEvent.EventType.ADD_TO_CART, product=product)
