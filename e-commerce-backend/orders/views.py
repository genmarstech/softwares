from django.shortcuts import get_object_or_404
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Address
from cart.models import Cart
from core.models import StoreSettings

from .models import Order
from .serializers import CheckoutSerializer, OrderSerializer
from .services import OrderError, cancel_order, create_order_from_cart

# A customer may self-cancel only before anything has been paid. Once an
# order is PAID or beyond, cancellation goes through support/admin — a
# broader set of transitions ALLOWED_TRANSITIONS permits for staff use.
CUSTOMER_CANCELLABLE = {Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED}


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    A customer only ever sees their own orders. Requesting someone else's
    order id is a 404, never a 403 — a 403 would confirm the id exists.
    """

    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return (
            Order.objects.filter(customer=self.request.user)
            .prefetch_related("items", "status_history")
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.status not in CUSTOMER_CANCELLABLE:
            return Response(
                {"detail": "This order can no longer be cancelled yourself — contact support."},
                status=400,
            )
        try:
            order = cancel_order(order, actor=request.user, reason="Cancelled by customer")
        except OrderError as exc:
            return Response({"detail": exc.safe_message}, status=400)
        return Response(OrderSerializer(order).data)


class CheckoutView(APIView):
    """
    Creates an order from the caller's current cart. All pricing/availability
    is re-validated server-side inside create_order_from_cart() — nothing
    from the request body is trusted for totals.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CheckoutSerializer

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        cart, _ = Cart.objects.get_or_create(user=request.user)

        if "address_id" in data:
            address = get_object_or_404(Address, pk=data["address_id"], user=request.user)
            delivery_fields = {
                "delivery_recipient_name": address.recipient_name,
                "delivery_line1": address.line1,
                "delivery_line2": address.line2,
                "delivery_city": address.city,
                "delivery_phone": address.phone_number,
            }
        else:
            delivery_fields = {
                "delivery_recipient_name": data["delivery_recipient_name"],
                "delivery_line1": data["delivery_line1"],
                "delivery_line2": data.get("delivery_line2", ""),
                "delivery_city": data["delivery_city"],
                "delivery_phone": data["delivery_phone"],
            }

        store_settings = StoreSettings.load()
        try:
            order = create_order_from_cart(
                request.user,
                cart,
                delivery_fee=store_settings.default_delivery_fee,
                contact_phone=data.get("contact_phone", ""),
                **delivery_fields,
            )
        except OrderError as exc:
            return Response({"detail": exc.safe_message}, status=400)

        return Response(OrderSerializer(order).data, status=201)
