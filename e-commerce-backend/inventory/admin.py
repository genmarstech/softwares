from django.contrib import admin

from .models import InventoryMovement, InventoryRecord
from .services import InsufficientStock, adjust_stock


class InventoryMovementInline(admin.TabularInline):
    model = InventoryMovement
    extra = 0
    readonly_fields = ["delta", "resulting_quantity", "reason", "reference", "note", "actor", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False  # movements are only ever created by adjust_stock()


@admin.register(InventoryRecord)
class InventoryRecordAdmin(admin.ModelAdmin):
    list_display = ["__str__", "quantity", "effective_threshold", "is_low_stock"]
    list_filter = []
    search_fields = ["product__name", "product__sku", "variant__sku"]
    readonly_fields = ["quantity"]
    inlines = [InventoryMovementInline]
    actions = ["restock_by_10"]

    @admin.action(description="Restock selected by +10 (demo action — use the record detail for a real amount)")
    def restock_by_10(self, request, queryset):
        for record in queryset:
            try:
                adjust_stock(record, 10, reason=InventoryMovement.Reason.RESTOCK, actor=request.user)
            except InsufficientStock:
                pass  # can't happen on a positive delta; kept for symmetry
        self.message_user(request, f"Restocked {queryset.count()} record(s) by 10.")
