from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated
from rest_framework import viewsets, authentication, permissions
from rest_framework.exceptions import PermissionDenied
from .serializers import BusinessSerializers, ServiceSerializer, BusinessStaffSerializer
from .models import Service, BusinessStaff
from rest_framework.authentication import SessionAuthentication, BasicAuthentication

from .models import Business

# Create your views here.
class BusinessViewSets(viewsets.ModelViewSet):

    serializer_class = BusinessSerializers

    authentication_classes = [SessionAuthentication, BasicAuthentication]

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Business.objects.filter(business_owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(business_owner=self.request.user)


class IsBusinessOwnerOrReadOnly(permissions.BasePermission):
  """Custom permission to ensure only the owner of the business can manage its services."""

  def has_object_permission(self, request, view, obj):
    # Safe methods (GET, HEAD, OPTIONS) are allowed for anyone
    if request.method in permissions.SAFE_METHODS:
      return True
    # Write permissions are only allowed to the owner of the business
    return obj.business.business_owner == request.user



class ServicesViewSet(viewsets.ModelViewSet):
  """ViewSet for viewing and editing business services using SessionAuthentication."""

  queryset = Service.objects.all()
  serializer_class = ServiceSerializer

  # Explicitly declare SessionAuthentication
  authentication_classes = [authentication.SessionAuthentication]

  # Enforce permissions
  permission_classes = [
      permissions.IsAuthenticatedOrReadOnly,
      IsBusinessOwnerOrReadOnly,
  ]

  def get_queryset(self):
    """Optimized Queryset: Uses select_related to prevent N+1 queries and allows filtering by business_id (e.g.,

    /api/services/?business_id=1).
    """
    queryset = super().get_queryset().select_related('business')
    business_id = self.request.query_params.get('business_id')

    if business_id:
      return queryset.filter(business_id=business_id)
    return queryset

  def perform_create(self, serializer):
    """Enforces that a user cannot create a service for a business they do not own."""
    business = serializer.validated_data['business']

    if business.business_owner != self.request.user:
      raise PermissionDenied(
          'You do not have permission to add services to a business you do not own.'
      )

    serializer.save()


class BusinessStaffViewSet(viewsets.ModelViewSet):

    serializer_class = BusinessStaffSerializer

    authentication_classes = [
        SessionAuthentication,
        BasicAuthentication
    ]

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = BusinessStaff.objects.filter(
            business__business_owner=self.request.user
        ).select_related("business")

        business_id = self.request.query_params.get("business")

        if business_id:
            queryset = queryset.filter(
                business_id=business_id
            )

        return queryset.order_by("-created_at")