from django.conf import settings
from django.db import models

from catalog.models import Product, ProductVariant
from core.models import StoreSettings, TimestampedModel


class InventoryRecord(TimestampedModel):
    """
    One row per sellable thing: a `Product` with no variants, OR each of its
    `ProductVariant`s — never both for the same product line. `quantity` is
    only ever changed through `services.adjust_stock()`; nothing else writes
    to this field.
    """

    product = models.OneToOneField(
        Product, null=True, blank=True, on_delete=models.CASCADE, related_name="inventory_record"
    )
    variant = models.OneToOneField(
        ProductVariant, null=True, blank=True, on_delete=models.CASCADE, related_name="inventory_record"
    )
    quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(
        null=True, blank=True, help_text="Blank uses the store-wide default."
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(product__isnull=False, variant__isnull=True)
                    | models.Q(product__isnull=True, variant__isnull=False)
                ),
                name="inventory_record_exactly_one_of_product_or_variant",
            )
        ]

    def __str__(self):
        return str(self.variant or self.product)

    @property
    def effective_threshold(self) -> int:
        if self.low_stock_threshold is not None:
            return self.low_stock_threshold
        return StoreSettings.load().low_stock_default_threshold

    @property
    def is_low_stock(self) -> bool:
        return self.quantity <= self.effective_threshold


class InventoryMovement(TimestampedModel):
    class Reason(models.TextChoices):
        RESTOCK = "restock", "Restock"
        ORDER = "order", "Order placed"
        CANCELLATION = "cancellation", "Order cancelled — stock restored"
        REFUND = "refund", "Order refunded — stock restored"
        RETURN = "return", "Customer return"
        ADJUSTMENT = "adjustment", "Manual adjustment"

    record = models.ForeignKey(InventoryRecord, on_delete=models.CASCADE, related_name="movements")
    delta = models.IntegerField(help_text="Positive adds stock, negative removes it.")
    resulting_quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    reference = models.CharField(max_length=100, blank=True, help_text="e.g. an order reference")
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.record} {self.delta:+d} ({self.reason})"
