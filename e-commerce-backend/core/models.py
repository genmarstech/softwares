from django.db import models


class TimestampedModel(models.Model):
    """Abstract base so created_at/updated_at aren't retyped on every model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def _default_business_id():
    # Deferred import: `businesses.models` imports `TimestampedModel` from
    # this module, so importing it back at module load time would be
    # circular. See businesses/models.py::default_business_id for what this
    # default is actually for (a Phase 1 → Phase 2 bridge) — this is the
    # same thing, just import-safe from this side of the circle.
    from businesses.models import default_business_id

    return default_business_id()


class StoreSettings(models.Model):
    """
    Currency and delivery defaults the rest of the store reads — one row per
    business, `business` being the real uniqueness key now (Phase 2). The
    `default=` bridge is still here deliberately: anything that calls
    `StoreSettings(...)` directly without going through `load(business)`
    (there shouldn't be any such call site left in application code after
    this phase, but a stray one would otherwise hit a plain NOT NULL error
    instead of a clear bug report) still resolves to the one business Phase
    1 seeded rather than crashing outright.
    """

    class Theme(models.TextChoices):
        """
        A storefront *design pattern*, not a color swap — each value maps
        to a genuinely different frontend layout (commerce-platform's
        src/lib/themes/registry.ts): grid density, card style, hero
        treatment, typography. New values are additive — the frontend's own
        registry is the other half of this enum and has to grow alongside
        it; an unrecognized value there falls back to DEFAULT rather than
        erroring, so adding a choice here first and shipping the frontend
        side a moment later never breaks a live store in between.
        """

        DEFAULT = "default", "Default — clean, general-purpose grid"
        FASHION = "fashion", "Fashion — editorial, lookbook-style"
        ELECTRONICS = "electronics", "Electronics — spec-forward, dense grid"
        GROCERY = "grocery", "Fresh/Grocery — vibrant, quick-add"

    business = models.OneToOneField(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="store_settings",
        default=_default_business_id,
    )
    currency = models.CharField(max_length=3, default="KES")
    default_delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    low_stock_default_threshold = models.PositiveIntegerField(default=5)
    theme = models.CharField(max_length=20, choices=Theme.choices, default=Theme.DEFAULT)

    class Meta:
        verbose_name = "Store settings"
        verbose_name_plural = "Store settings"

    def __str__(self):
        return "Store settings"

    @classmethod
    def load(cls, business) -> "StoreSettings":
        obj, _ = cls.objects.get_or_create(business=business)
        return obj
