from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "provider", "amount", "currency", "status", "receipt_number", "created_at"]
    list_filter = ["status", "provider"]
    search_fields = ["order__reference", "checkout_request_id", "receipt_number", "phone_number"]
    readonly_fields = [f.name for f in Payment._meta.fields]  # audit record — never hand-edited

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
