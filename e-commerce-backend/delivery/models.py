from django.db import models

from core.models import TimestampedModel
from orders.models import Order


class Shipment(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        DELIVERED = "delivered", "Delivered"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    method = models.CharField(max_length=50, default="courier")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    courier_name = models.CharField(max_length=150, blank=True)
    tracking_reference = models.CharField(max_length=100, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Shipment for {self.order.reference}"
