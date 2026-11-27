"""Tenant isolation helpers shared by every business-owned API.

Every request that touches business data must name the business it is working
on (``?business=<id>`` on reads, ``business`` in the body on writes). The mixin
resolves that id **only among businesses the signed-in user owns**, so one
tenant can never read or change another tenant's records, even by guessing ids.
"""

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated

from .models import Business


def owned_businesses(user):
    if not user or not user.is_authenticated:
        return Business.objects.none()
    return Business.objects.filter(business_owner=user)


class BusinessScopedMixin:
    """Scope a ViewSet's queryset and writes to one business the user owns.

    Set ``business_lookup`` to the ORM path from the model to its Business
    (``"business"`` by default, e.g. ``"staff__business"`` for nested models).
    """

    permission_classes = [IsAuthenticated]
    business_lookup = "business"
    business_param = "business"
    require_business_param = True

    def _raw_business_id(self):
        value = self.request.query_params.get(self.business_param)
        if value in (None, "") and self.request.method not in ("GET", "HEAD", "OPTIONS"):
            data = getattr(self.request, "data", None) or {}
            value = data.get(self.business_param) if hasattr(data, "get") else None
        # Accept the older ``business_id`` spelling the frontend used to send.
        if value in (None, ""):
            value = self.request.query_params.get("business_id")
        return value

    def get_business(self, required=None):
        if hasattr(self, "_business_cache"):
            return self._business_cache
        required = self.require_business_param if required is None else required
        raw = self._raw_business_id()
        if raw in (None, ""):
            if required:
                raise ValidationError({self.business_param: ["This query parameter is required."]})
            self._business_cache = None
            return None
        try:
            business_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({self.business_param: ["Must be a business id."]})
        business = owned_businesses(self.request.user).filter(pk=business_id).first()
        if business is None:
            raise NotFound("Business not found.")
        self._business_cache = business
        return business

    def scope_queryset(self, queryset):
        user_filter = {f"{self.business_lookup}__business_owner": self.request.user}
        queryset = queryset.filter(**user_filter)
        # Detail routes (retrieve/update/delete) work by id across the user's own
        # businesses; list routes must name the business.
        is_list = getattr(self, "action", None) == "list"
        business = self.get_business(required=is_list and self.require_business_param)
        if business is not None:
            queryset = queryset.filter(**{self.business_lookup: business})
        return queryset

    def get_queryset(self):
        return self.scope_queryset(super().get_queryset())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["business_owner"] = self.request.user
        return context
