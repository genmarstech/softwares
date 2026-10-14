from django.contrib import admin
from django.utils import timezone

from .models import Shipment


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["order", "method", "status", "courier_name", "tracking_reference"]
    list_filter = ["status", "method"]
    search_fields = ["order__reference", "tracking_reference", "courier_name"]
    autocomplete_fields = []
    actions = ["mark_dispatched", "mark_delivered"]

    @admin.action(description="Mark dispatched")
    def mark_dispatched(self, request, queryset):
        queryset.update(status=Shipment.Status.DISPATCHED, dispatched_at=timezone.now())

    @admin.action(description="Mark delivered")
    def mark_delivered(self, request, queryset):
        queryset.update(status=Shipment.Status.DELIVERED, delivered_at=timezone.now())
