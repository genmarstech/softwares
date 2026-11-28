"""Chat bots: one Velora bot on Telegram and one Velora number on WhatsApp.

Every business shares the same bot. A customer arrives through a business's
own link (``t.me/<bot>?start=<slug>`` or ``wa.me/<number>?text=... (ref: <slug>)``),
so the bot knows which business they want, and finds or creates their
``customers.CustomerAccount`` by the phone number the chat app vouches for.
"""

import secrets

from django.db import models

from business.models import Business


class BotChannel(models.TextChoices):
    TELEGRAM = 'telegram', 'Telegram'
    WHATSAPP = 'whatsapp', 'WhatsApp'


class BotSettings(models.Model):
    """Which bots a business uses, and where its own booking alerts go."""

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name='bot_settings')
    telegram_enabled = models.BooleanField(default=False)
    whatsapp_enabled = models.BooleanField(default=False)

    # The owner links their own Telegram from the dashboard to get new-booking
    # alerts with Confirm / Decline buttons.
    owner_telegram_chat_id = models.CharField(max_length=64, blank=True, default='')
    owner_telegram_name = models.CharField(max_length=255, blank=True, default='')
    owner_link_token = models.CharField(max_length=32, blank=True, default='')
    # Alerts to the business's WhatsApp number (Business.whatsapp_number).
    alert_owner_whatsapp = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'bot settings'

    def enabled(self, channel):
        return bool(getattr(self, f'{channel}_enabled', False))

    def new_owner_link_token(self):
        self.owner_link_token = secrets.token_urlsafe(18)[:24].replace('-', 'x').replace('_', 'y')
        self.save(update_fields=['owner_link_token', 'updated_at'])
        return self.owner_link_token

    def __str__(self):
        return f'Bots for {self.business.business_name}'


class ChatIdentity(models.Model):
    """One chat with the bot: a Telegram chat or a WhatsApp number."""

    channel = models.CharField(max_length=20, choices=BotChannel.choices)
    # Telegram chat id, or the WhatsApp number in Meta's format (254712345678).
    external_id = models.CharField(max_length=64)
    display_name = models.CharField(max_length=255, blank=True, default='')
    phone_number = models.CharField(max_length=20, blank=True, default='', help_text='Verified, E.164')
    account = models.ForeignKey(
        'customers.CustomerAccount', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='chat_identities',
    )
    # The business this chat is currently booking with.
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    state = models.JSONField(default=dict, blank=True)
    opted_out = models.BooleanField(default=False, help_text='Sent STOP: no reminders or updates')
    # WhatsApp only allows free-form messages within 24 hours of the customer's last message.
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'chat identities'
        constraints = [
            models.UniqueConstraint(fields=['channel', 'external_id'], name='unique_chat_identity'),
        ]
        indexes = [models.Index(fields=['channel', 'phone_number'])]

    def __str__(self):
        return f'{self.get_channel_display()} {self.display_name or self.external_id}'


class InboundUpdate(models.Model):
    """Ids of updates already handled, so a webhook retried by Telegram or Meta runs once."""

    channel = models.CharField(max_length=20, choices=BotChannel.choices)
    external_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['channel', 'external_id'], name='unique_inbound_update'),
        ]
