from django.db import models


class TimestampedModel(models.Model):
    """Abstract base so created_at/updated_at aren't retyped on every model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class StoreSettings(models.Model):
    """
    Singleton row: currency and delivery defaults the rest of the store reads.

    Enforced as a singleton by always loading/saving pk=1 (see `load()`) rather
    than by a DB constraint — simplest thing that works for one row.
    """

    currency = models.CharField(max_length=3, default="KES")
    default_delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    low_stock_default_threshold = models.PositiveIntegerField(default=5)

    class Meta:
        verbose_name = "Store settings"
        verbose_name_plural = "Store settings"

    def __str__(self):
        return "Store settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "StoreSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
