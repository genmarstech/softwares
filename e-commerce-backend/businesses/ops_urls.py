from rest_framework.routers import DefaultRouter

from . import ops_views

router = DefaultRouter()
router.register("members", ops_views.OpsMembershipViewSet, basename="ops-member")

urlpatterns = router.urls
