from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("cart/items", views.CartItemViewSet, basename="cart-item")

urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart"),
] + router.urls
