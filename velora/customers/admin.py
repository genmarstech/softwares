from django.contrib import admin

from .models import CustomerAccount


@admin.register(CustomerAccount)
class CustomerAccountAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'phone_number', 'email', 'is_active', 'last_login', 'created_at']
    search_fields = ['full_name', 'phone_number', 'email']
    list_filter = ['is_active']
    exclude = ['password']
