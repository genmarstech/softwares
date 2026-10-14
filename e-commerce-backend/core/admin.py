from django.contrib import admin

from .models import StoreSettings


@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    # One row per business (the `business` OneToOneField is the uniqueness
    # key) — no longer a platform-wide singleton as of Phase 2, so unlike
    # before, a second row isn't a bug; it's a second business's settings.
    list_display = ("business", "currency", "default_delivery_fee", "low_stock_default_threshold")
    autocomplete_fields = ["business"]
