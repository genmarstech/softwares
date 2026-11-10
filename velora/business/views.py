from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated
from rest_framework import viewsets
from .serializers import BusinessSerializers
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