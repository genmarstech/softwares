from django.urls import path

from . import views

urlpatterns = [
    path('telegram/webhook/', views.TelegramWebhookView.as_view(), name='telegram-webhook'),
    path('whatsapp/webhook/', views.WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),
    path('settings/', views.ChannelSettingsView.as_view(), name='bot-settings'),
]
