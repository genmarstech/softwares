from rest_framework import permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Cart, CartItem
from .serializers import CartItemSerializer, CartSerializer


class CartView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CartSerializer

    def get(self, request):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        return Response(CartSerializer(cart).data)


class CartItemViewSet(viewsets.ModelViewSet):
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()
        return CartItem.objects.filter(cart__user=self.request.user).select_related("product", "variant")

    def perform_create(self, serializer):
        cart, _ = Cart.objects.get_or_create(user=self.request.user)
        product = serializer.validated_data["product"]
        variant = serializer.validated_data.get("variant")

        existing = CartItem.objects.filter(cart=cart, product=product, variant=variant).first()
        if existing:
            existing.quantity += serializer.validated_data["quantity"]
            existing.save(update_fields=["quantity"])
            serializer.instance = existing
        else:
            serializer.save(cart=cart)
