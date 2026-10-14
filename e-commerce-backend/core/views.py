"""
The two pieces of "admin operations" that don't belong to any one app:
store-wide settings, and a cross-app dashboard summary.

Settings is owner/manager only, not staff — "settings privileges" are
explicitly called out as something staff doesn't inherit just by being a
business member (see businesses/permissions.py). The dashboard stays open
to any active member — nothing in the plan restricts it, and a staff
member fulfilling orders has a reasonable case for seeing today's queue.
"""

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessManager, IsBusinessStaff
from inventory.models import InventoryRecord
from orders.models import Order
from orders.serializers import OrderSummarySerializer

from .models import StoreSettings
from .serializers import StoreSettingsSerializer

# A sale "counts" once payment has gone through, whatever happens to
# fulfillment after that — cancelled/refunded orders are excluded entirely
# rather than netted out, since a refund already has its own audit trail in
# inventory movements and shouldn't quietly erase itself from the sales line.
COMPLETED_SALE_STATUSES = [
    Order.Status.PAID, Order.Status.PROCESSING, Order.Status.SHIPPED, Order.Status.DELIVERED,
]


class StoreSettingsView(BusinessScopedMixin, APIView):
    permission_classes = [IsBusinessManager]
    serializer_class = StoreSettingsSerializer

    def get(self, request, business_slug=None):
        return Response(StoreSettingsSerializer(StoreSettings.load(self.business)).data)

    def patch(self, request, business_slug=None):
        serializer = StoreSettingsSerializer(
            StoreSettings.load(self.business), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class DashboardView(BusinessScopedMixin, APIView):
    permission_classes = [IsBusinessStaff]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request, business_slug=None):
        sales_total = Order.objects.filter(
            business=self.business, status__in=COMPLETED_SALE_STATUSES
        ).aggregate(
            total=Coalesce(Sum("total"), Value(0), output_field=DecimalField(max_digits=12, decimal_places=2))
        )["total"]

        orders_by_status = {
            row["status"]: row["count"]
            for row in Order.objects.filter(business=self.business).values("status").annotate(count=Count("id"))
        }

        # is_low_stock is a Python property (its threshold can fall back to
        # StoreSettings), not a DB column, so this is a plain loop rather than
        # a queryset filter — fine at the record counts an MVP store has.
        low_stock_count = sum(
            1 for record in InventoryRecord.objects.filter(business=self.business) if record.is_low_stock
        )

        recent_orders = Order.objects.filter(business=self.business).order_by("-created_at")[:10]

        return Response(
            {
                "sales_total": sales_total,
                "orders_by_status": orders_by_status,
                "low_stock_count": low_stock_count,
                "recent_orders": OrderSummarySerializer(recent_orders, many=True).data,
            }
        )
