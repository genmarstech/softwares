from rest_framework.routers import DefaultRouter

from .ops_views import OpsPaymentViewSet

router = DefaultRouter()
router.register("payments", OpsPaymentViewSet, basename="ops-payment")

urlpatterns = router.urls
