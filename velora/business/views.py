from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .mixins import BusinessScopedMixin
from .models import Business, Service, BusinessStaff
from .serializers import BusinessSerializers, ServiceSerializer, BusinessStaffSerializer


class BusinessViewSets(viewsets.ModelViewSet):
    """The signed-in owner's businesses."""

    serializer_class = BusinessSerializers
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Business.objects.filter(business_owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        business = serializer.save(business_owner=self.request.user)
        # Every business starts with a booking policy and default opening hours,
        # so the availability engine has something to work with straight away.
        from bookings.services import ensure_business_defaults
        ensure_business_defaults(business)


class ServicesViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    """Services of one business owned by the signed-in user.

    Customers see services through the public booking API instead, which only
    exposes what a booking page needs.
    """

    queryset = Service.objects.select_related('business').order_by('service_name')
    serializer_class = ServiceSerializer


class BusinessStaffViewSet(BusinessScopedMixin, viewsets.ModelViewSet):

    queryset = BusinessStaff.objects.select_related('business').prefetch_related('skills').order_by('-created_at')
    serializer_class = BusinessStaffSerializer
