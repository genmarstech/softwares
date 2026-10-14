from django.conf import settings
from django.db import models

from catalog.models import Product, ProductVariant
from core.models import TimestampedModel


class Cart(TimestampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")

    def __str__(self):
        return f"Cart({self.user.email})"


class CartItem(TimestampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("cart", "product", "variant")]
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"

    @property
    def unit_price(self):
        # Display-only. Checkout re-reads live Product/ProductVariant price —
        # never trusts whatever this cart item last saw.
        return self.variant.price if self.variant else self.product.price
