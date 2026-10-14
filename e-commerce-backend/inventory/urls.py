from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("inventory", views.InventoryRecordViewSet, basename="inventory-record")

urlpatterns = router.urls
