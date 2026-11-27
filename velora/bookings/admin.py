from django.contrib import admin

from .models import (
    Booking, BookingEvent, BookingPolicy, Customer, FunnelEvent, Location, Payment, Resource, TimeOff,
    WaitlistEntry, WorkingHours,
)


class BookingEventInline(admin.TabularInline):
    model = BookingEvent
    extra = 0
    readonly_fields = [f.name for f in BookingEvent._meta.fields]
    can_delete = False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'customer', 'service', 'staff', 'start', 'status', 'channel')
    list_filter = ('status', 'channel', 'business')
    search_fields = ('customer__full_name', 'customer__email', 'customer__phone_number')
    inlines = [PaymentInline, BookingEventInline]
    readonly_fields = ('manage_token', 'created_at', 'updated_at')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'phone_number', 'business')
    search_fields = ('full_name', 'email', 'phone_number')


for model in (BookingPolicy, Location, Resource, WorkingHours, TimeOff, WaitlistEntry, FunnelEvent):
    admin.site.register(model)
