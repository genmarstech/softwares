from rest_framework.routers import DefaultRouter

from .ops_views import OpsOrderViewSet

router = DefaultRouter()
router.register("orders", OpsOrderViewSet, basename="ops-order")

urlpatterns = router.urls
