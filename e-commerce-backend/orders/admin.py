from django.contrib import admin, messages

from .models import Order, OrderItem, OrderStatusHistory
from .services import OrderError, transition


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ["product", "variant", "product_name", "sku", "unit_price", "quantity", "line_total"]

    def has_add_permission(self, request, obj=None):
        return False


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ["from_status", "to_status", "note", "actor", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


def _transition_action(to_status, label):
    """
    One admin action per target status, all going through orders.services.
    transition() — status is read-only on the form itself (see OrderAdmin
    below) so this is the only way an admin changes it, and every change
    gets an OrderStatusHistory row and a customer email for free.
    """

    @admin.action(description=label)
    def action(modeladmin, request, queryset):
        moved, skipped = 0, 0
        for order in queryset:
            try:
                transition(order, to_status, actor=request.user, note="Changed via admin")
                moved += 1
            except OrderError as exc:
                skipped += 1
                modeladmin.message_user(
                    request, f"{order.reference}: {exc.safe_message}", level=messages.WARNING
                )
        modeladmin.message_user(request, f"Moved {moved} order(s); {skipped} skipped.")

    action.__name__ = f"mark_{to_status}"
    return action


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["reference", "customer", "status", "total", "currency", "created_at"]
    list_filter = ["status", "currency"]
    search_fields = ["reference", "customer__email", "contact_email", "contact_phone"]
    readonly_fields = [
        "reference", "customer", "status", "currency",
        "subtotal", "delivery_fee", "discount_total", "tax_total", "total",
        "contact_email", "contact_phone",
        "delivery_recipient_name", "delivery_line1", "delivery_line2",
        "delivery_city", "delivery_phone", "stock_restored",
        "created_at", "updated_at",
    ]
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    actions = [
        _transition_action(Order.Status.PROCESSING, "Mark as processing"),
        _transition_action(Order.Status.SHIPPED, "Mark as shipped"),
        _transition_action(Order.Status.DELIVERED, "Mark as delivered"),
        _transition_action(Order.Status.CANCELLED, "Cancel order (restores stock)"),
        _transition_action(Order.Status.REFUNDED, "Refund order (restores stock)"),
    ]

    def has_add_permission(self, request):
        return False  # orders are only ever created through checkout

    def has_delete_permission(self, request, obj=None):
        return False  # preserve purchase history — see the checklist's own rule
