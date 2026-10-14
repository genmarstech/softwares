from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import mixins, permissions, viewsets

from .models import User
from .serializers import CustomerSerializer


class OpsCustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Staff-only customer list. Never includes other staff accounts —
    customers and colleagues share a table but not a listing."""

    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAdminUser]
    search_fields = ["email", "first_name", "last_name", "phone_number"]
    ordering_fields = ["date_joined", "lifetime_total", "order_count"]

    def get_queryset(self):
        return (
            User.objects.filter(is_staff=False)
            .annotate(
                order_count=Count("orders", distinct=True),
                lifetime_total=Coalesce(
                    Sum("orders__total"), Value(0), output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
            )
            .order_by("-date_joined")
        )
