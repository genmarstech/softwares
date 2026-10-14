from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import mixins, viewsets

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessStaff

from .models import User
from .serializers import CustomerSerializer


class OpsCustomerViewSet(BusinessScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    This business's own customers — never other staff accounts, and never
    a customer's activity with a DIFFERENT business. The same person can
    have ordered from business A and business B (one account, Decision 2 of
    the plan); each business's list, and each business's view of that
    person's `order_count`/`lifetime_total`, must only ever reflect its own
    orders — hence filtering the annotations themselves by this business,
    not just the base queryset.
    """

    serializer_class = CustomerSerializer
    permission_classes = [IsBusinessStaff]
    search_fields = ["email", "first_name", "last_name", "phone_number"]
    ordering_fields = ["date_joined", "lifetime_total", "order_count"]

    def get_queryset(self):
        in_this_business = Q(orders__business=self.business)
        return (
            User.objects.filter(is_staff=False, orders__business=self.business)
            .annotate(
                order_count=Count("orders", filter=in_this_business, distinct=True),
                lifetime_total=Coalesce(
                    Sum("orders__total", filter=in_this_business),
                    Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .distinct()
            .order_by("-date_joined")
        )
