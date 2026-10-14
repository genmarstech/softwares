from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

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
        cart, _ = Cart.objects.get_or_create(user=self.request.user, business=self.business)
        product = serializer.validated_data["product"]
        variant = serializer.validated_data.get("variant")

        # A business's storefront must never let a customer add another
        # business's product to the cart it's resolved for — the product
        # picker only ever offers this business's own catalog, so reaching
        # this means the request didn't come from that picker.
        owner = variant.product if variant else product
        if owner.business_id != self.business.pk:
            raise ValidationError({"product": "That product is not sold by this business."})

        existing = CartItem.objects.filter(cart=cart, product=product, variant=variant).first()
        if existing:
            existing.quantity += serializer.validated_data["quantity"]
            existing.save(update_fields=["quantity"])
            serializer.instance = existing
        else:
            serializer.save(cart=cart)
