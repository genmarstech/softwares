import secrets

from django.conf import settings
from django.db import models

from businesses.models import Business, default_business_id
from catalog.models import Product, ProductVariant
from core.models import TimestampedModel


def generate_reference() -> str:
    """A non-guessable order reference — never a sequential id."""
    return f"GM-{secrets.token_hex(6).upper()}"


class Order(TimestampedModel):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        PAYMENT_FAILED = "payment_failed", "Payment failed"
        PAID = "paid", "Paid"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    reference = models.CharField(max_length=20, unique=True, default=generate_reference, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    business = models.ForeignKey(
        Business, on_delete=models.PROTECT, related_name="orders", default=default_business_id
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_PAYMENT)

    currency = models.CharField(max_length=3, default="KES")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=20)

    # Delivery address is copied onto the order at checkout time — fields,
    # not a FK to accounts.Address — so editing or deleting a saved address
    # later never changes what an existing order says it was delivered to.
    delivery_recipient_name = models.CharField(max_length=150)
    delivery_line1 = models.CharField(max_length=255)
    delivery_line2 = models.CharField(max_length=255, blank=True)
    delivery_city = models.CharField(max_length=100)
    delivery_phone = models.CharField(max_length=20)

    # Set once by orders.services.transition() the first time the order
    # enters CANCELLED/REFUNDED, so a later transition through those states
    # (e.g. a theoretical cancelled->refunded path) can never double-restore
    # the same stock.
    stock_restored = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["customer", "status"])]

    def __str__(self):
        return self.reference


class OrderItem(models.Model):
    """
    A purchase-time snapshot. `product` is kept for convenience (e.g. linking
    back from an order to the live product page) but every field that matters
    for "what did the customer actually buy and pay" is copied here and never
    re-read from `product` after creation.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL, related_name="order_items")
    # Which variant (if any) this line was for — needed so a later stock
    # restoration (cancel/refund) credits the SAME InventoryRecord that was
    # debited at checkout, not the product's own record (which may not even
    # exist once the product has variants — see InventoryRecord's constraint).
    variant = models.ForeignKey(
        ProductVariant, null=True, on_delete=models.SET_NULL, related_name="order_items"
    )
    product_name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.product_name} ({self.order.reference})"


class OrderStatusHistory(TimestampedModel):
    """Append-only. A correction is a new row, never an edit to an old one."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "order status history"

    def __str__(self):
        return f"{self.order.reference}: {self.from_status or '—'} → {self.to_status}"
