from django.contrib import admin

from .models import BotSettings, ChatIdentity


@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    list_display = ('business', 'telegram_enabled', 'whatsapp_enabled', 'owner_telegram_name', 'alert_owner_whatsapp')
    exclude = ('owner_link_token',)


@admin.register(ChatIdentity)
class ChatIdentityAdmin(admin.ModelAdmin):
    list_display = ('channel', 'display_name', 'phone_number', 'account', 'business', 'opted_out', 'last_inbound_at')
    list_filter = ('channel', 'opted_out')
    search_fields = ('display_name', 'phone_number', 'external_id')
