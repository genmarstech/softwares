"""
The tenant model. A `Business` is one independent store on the shared
platform; everything it owns (catalog, inventory, carts, orders, store
settings) carries a `business` FK added across Phase 1's migrations.

`BusinessMembership` is deliberately a separate table from `accounts.User`,
not a field on it — a user's relationship to a business is a row here, not
an attribute of their account, which is what makes "one account, many
businesses" (Decision 2 of the multi-tenant plan) possible without the
identity table itself knowing how many businesses someone shops at or works
for.

Nothing here touches `User.is_staff`. That field is the platform-admin gate
(Genmars operating the platform) and stays completely separate from business
membership roles (a business's own owner/manager/staff) — see the plan's
Decision 1. No serializer or view in this app may ever accept `is_staff` as
an input; that boundary is enforced in the views that get written in a later
phase, but the separation starts here, in the schema: there is no field on
either model that could carry that privilege across.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from core.models import TimestampedModel

# Slugs that would collide with a fixed top-level route under /api/ or
# /api/ops/ (auth/, me/, addresses/, ops/, platform/, schema/, docs/) or with
# Django's own /admin/, /static/, /media/. A business taking one of these
# would make its own storefront unreachable or shadow a fixed route — this
# list is the whole reason Business.slug has a validator instead of just a
# uniqueness constraint.
RESERVED_SLUGS = frozenset(
    {
        "api", "ops", "platform", "admin", "auth", "me", "addresses",
        "schema", "docs", "static", "media", "health",
    }
)


def validate_business_slug(value: str) -> None:
    if value in RESERVED_SLUGS:
        raise ValidationError(f"'{value}' is a reserved slug and cannot be used for a business.")


SEEDED_BUSINESS_SLUG = "genmars-store"


def default_business_id():
    """
    A Phase 1 → Phase 2 bridge, not a permanent design choice.

    `business` just became a required field on several models (Category,
    Product, InventoryRecord, Cart, Order, StoreSettings) in the same
    migration sequence that backfilled every existing row onto the one
    business this bridge points at. Until Phase 2 wires real tenant
    resolution into every view and test fixture that creates one of those
    rows, this default is what keeps `Model.objects.create(...)` calls that
    don't yet pass `business=` explicitly working, rather than breaking the
    entire existing test suite and every current view the moment the column
    stopped being nullable.

    Once Phase 2 lands and every create call site passes `business`
    explicitly, nothing should still be hitting this default — it should
    come out of every field below at that point, not stay as a silent
    "assume the first business" fallback in a system meant to hold many.
    """
    return Business.objects.filter(slug=SEEDED_BUSINESS_SLUG).values_list("pk", flat=True).first()


class Business(TimestampedModel):
    """
    One tenant. `slug` is the routing key in `/api/<slug>/...` and
    `/api/ops/<slug>/...` — see `validate_business_slug` for why it can't be
    just any string, and `businesses/services.py::create_business` for the
    one place this model should be constructed from (it runs that validator
    via `full_clean()`, which a bare `Business.objects.create()` would skip).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        OFFBOARDING = "offboarding", "Offboarding"

    name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=60, unique=True, validators=[validate_business_slug]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    country = models.CharField(max_length=2, default="KE", help_text="ISO 3166-1 alpha-2")
    timezone = models.CharField(max_length=50, default="Africa/Nairobi")
    default_currency = models.CharField(max_length=3, default="KES")

    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "businesses"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class BusinessMembership(TimestampedModel):
    """
    A user's role within exactly one business. Never confuse this with
    `User.is_staff` (the platform-admin flag, defined on `accounts.User`,
    untouched by anything in this app) — a founder who is also a Genmars
    platform admin has BOTH an `is_staff=True` row and, separately, an
    `owner` membership here; neither implies the other, and nothing in this
    model can grant `is_staff`.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        STAFF = "staff", "Staff"

    class MembershipStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        SUSPENDED = "suspended", "Suspended"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    status = models.CharField(
        max_length=10, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memberships_invited",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "user"], name="one_membership_per_business_user")
        ]
        ordering = ["business", "role", "-created_at"]

    def __str__(self) -> str:
        return f"{self.user.email} — {self.business.name} ({self.role})"
