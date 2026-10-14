from django.db import models
from django.utils.text import slugify

from businesses.models import Business, default_business_id
from core.models import TimestampedModel

# `business` went through the Phase 1 migration sequence: added nullable,
# backfilled onto the one seeded business (businesses/migrations/0002), and
# is now required — see catalog/migrations/0003 for the non-null flip. It
# stays required from here on; nothing in application code should ever see
# or need to handle a null value on either model below.
#
# Category.name/slug and Product.slug/sku are unique PER BUSINESS, not
# globally — two stores must each be able to have an "Electronics" category
# or a "SKU-1" product. This was flagged as a deferred gap when `business`
# was first added and fixed here, in Phase 3, once a real cross-business
# test actually collided on it (two businesses' default-named test fixtures
# both slugifying to "test-product").
#
# ProductVariant.sku is ALSO business-scoped (fixed in the consolidated
# review that follows Phase 3 — flagged there as a residual, confirmed by
# checking existing data for collisions before migrating: zero, across the
# only business that had any variants at the time). `business` is carried
# directly on the row, same reasoning as InventoryRecord: one FK beats a
# two-hop `product__business` filter everywhere this needs to be checked.


class Category(TimestampedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    business = models.ForeignKey(
        Business, on_delete=models.PROTECT, related_name="categories", default=default_business_id
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_category_name_per_business"),
            models.UniqueConstraint(fields=["business", "slug"], name="unique_category_slug_per_business"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, blank=True)
    description = models.TextField(blank=True)
    sku = models.CharField(max_length=64)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    business = models.ForeignKey(
        Business, on_delete=models.PROTECT, related_name="products", default=default_business_id
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]
        constraints = [
            models.UniqueConstraint(fields=["business", "slug"], name="unique_product_slug_per_business"),
            models.UniqueConstraint(fields=["business", "sku"], name="unique_product_sku_per_business"),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED


class ProductImage(TimestampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"Image for {self.product.name}"


class ProductVariant(TimestampedModel):
    """
    Optional — a product with no variants is a complete, sellable product on
    its own (inventory.InventoryRecord can point at the product directly).
    Only used when the store actually needs size/colour-style options.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    business = models.ForeignKey(
        Business, on_delete=models.PROTECT, related_name="product_variants", default=default_business_id
    )
    name = models.CharField(max_length=100, help_text="e.g. Size, Colour")
    value = models.CharField(max_length=100, help_text="e.g. M, Red")
    sku = models.CharField(max_length=64)
    price_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = [("product", "name", "value")]
        ordering = ["name", "value"]
        constraints = [
            models.UniqueConstraint(fields=["business", "sku"], name="unique_variant_sku_per_business"),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.name}: {self.value}"

    @property
    def price(self):
        return self.price_override if self.price_override is not None else self.product.price
