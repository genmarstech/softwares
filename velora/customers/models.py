from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class CustomerAccount(models.Model):
    """One person who books with Velora businesses.

    The account is shared across businesses: each business they join gets its
    own ``bookings.Customer`` record (linked through ``Customer.account``), so
    every business keeps its own notes and preferences for them.

    The phone number is the identity. Future WhatsApp and Telegram bots find
    the account by the number they see, so it is unique and stored in E.164.
    """

    phone_number = models.CharField(max_length=20, unique=True, help_text='E.164, e.g. +254712345678')
    full_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default='')
    password = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        def upgrade(raw):
            self.set_password(raw)
            self.save(update_fields=['password'])
        return check_password(raw_password, self.password, upgrade)

    def __str__(self):
        return f'{self.full_name} ({self.phone_number})'
