from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('event', 'channel', 'to_address', 'status', 'scheduled_for', 'attempts', 'business')
    list_filter = ('status', 'channel', 'event')
    search_fields = ('to_address', 'recipient_name')
