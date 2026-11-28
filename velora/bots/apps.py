from django.apps import AppConfig


class BotsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bots'
    verbose_name = 'Telegram and WhatsApp bots'

    def ready(self):
        from notifications.providers import PROVIDERS

        from .providers import PROVIDERS as BOT_PROVIDERS
        PROVIDERS.update(BOT_PROVIDERS)
