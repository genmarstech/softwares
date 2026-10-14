from django.conf import settings
from django.db import models

from businesses.models import Business, default_business_id
from catalog.models import Product, ProductVariant
from core.models import TimestampedModel


class Cart(TimestampedModel):
    # One cart per (user, business) as of Phase 3 — was a OneToOneField on
    # user alone through Phases 1-2 (one cart per user, full stop), which is
    # exactly what made holding a cart at two businesses at once raise an
    # IntegrityError. Every existing row already satisfies this looser
    # constraint trivially (it already had at most one row per user, which
    # is a special case of at most one row per (user, business)), so this
    # migration needs no data backfill — see
    # cart/migrations/0004_alter_cart_user_cart_one_cart_per_user_per_business.py.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carts")
    business = models.ForeignKey(
        Business, on_delete=models.PROTECT, related_name="carts", default=default_business_id
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "business"], name="one_cart_per_user_per_business")
        ]

    def __str__(self):
        return f"Cart({self.user.email}, {self.business.slug})"


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
