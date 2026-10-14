from django.db import models

from core.models import TimestampedModel
from orders.models import Order


class Payment(TimestampedModel):
    """
    One row per payment ATTEMPT — an order can have more than one if a first
    STK push times out or is cancelled and the customer retries. Only the
    callback (see payments/views.py) ever moves a row out of PENDING; nothing
    else is allowed to mark one SUCCESSFUL.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=20, default="mpesa")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    phone_number = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Daraja's own identifiers — CheckoutRequestID is the only handle that
    # ties the eventual callback back to this row, so it must be unique and
    # is what the callback looks the row up by.
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    receipt_number = models.CharField(max_length=50, blank=True)

    raw_callback = models.JSONField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.reference} — {self.provider} — {self.status}"
