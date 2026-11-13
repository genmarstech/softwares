from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BusinessViewSets, ServicesViewSet, BusinessStaffViewSet


router = DefaultRouter()

router.register(r"business", BusinessViewSets, basename='business')
router.register(r"services", ServicesViewSet, basename='services')
router.register(r'staff', BusinessStaffViewSet, basename='staff')

urlpatterns = router.urls