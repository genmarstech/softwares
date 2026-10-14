from django.contrib import admin

from .models import StoreSettings


@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    list_display = ("currency", "default_delivery_fee", "low_stock_default_threshold")

    def has_add_permission(self, request):
        # Singleton — only the one row StoreSettings.load() creates.
        return not StoreSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
