from django.contrib import admin

from .models import Business, Service, BusinessStaff


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'business_category', 'business_owner', 'created_at')
    list_filter = ('business_category',)
    search_fields = ('business_name', 'business_email')


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('service_name', 'business', 'price', 'duration_minutes', 'is_available')
    list_filter = ('is_available',)


@admin.register(BusinessStaff)
class BusinessStaffAdmin(admin.ModelAdmin):
    list_display = ('staff_name', 'staff_email', 'business', 'is_available')
