"""
`/api/ops/platform/businesses/` — Genmars operating the platform itself,
not any one business. `IsAdminUser` (`User.is_staff`) only; a business
owner has no path to this view regardless of their role, because nothing
here checks `BusinessMembership` at all. Deliberately thin, per the plan:
create, list, retrieve, activate, suspend. No impersonation, no "log in as
this business" — a platform admin who needs to help a business gets a
BusinessMembership the normal way, same as anyone else.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .models import Business
from .serializers import BusinessSerializer
from .services import create_business


class PlatformBusinessViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    queryset = Business.objects.all()
    serializer_class = BusinessSerializer
    permission_classes = [permissions.IsAdminUser]
    search_fields = ["name", "slug"]
    ordering_fields = ["name", "created_at"]

    def perform_create(self, serializer):
        # Routed through the service, not serializer.save(), so the reserved
        # -slug validator and auto-slugging actually run — see
        # businesses/services.py::create_business.
        try:
            business = create_business(**serializer.validated_data)
        except DjangoValidationError as exc:
            raise DRFValidationError(getattr(exc, "message_dict", exc.messages))
        serializer.instance = business

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        business = self.get_object()
        business.status = Business.Status.ACTIVE
        business.save(update_fields=["status"])
        return Response(BusinessSerializer(business).data)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        business = self.get_object()
        business.status = Business.Status.SUSPENDED
        business.save(update_fields=["status"])
        return Response(BusinessSerializer(business).data)
