from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BusinessViewSets


router = DefaultRouter()

router.register(r"business", BusinessViewSets, basename='business')

urlpatterns = router.urls