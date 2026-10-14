from rest_framework.routers import DefaultRouter

from . import platform_views

router = DefaultRouter()
router.register("businesses", platform_views.PlatformBusinessViewSet, basename="platform-business")

urlpatterns = router.urls
