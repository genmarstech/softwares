from rest_framework.routers import DefaultRouter

from .ops_views import OpsCustomerViewSet

router = DefaultRouter()
router.register("customers", OpsCustomerViewSet, basename="ops-customer")

urlpatterns = router.urls
