"""
Phase 1 data migration — assigns every pre-existing row to one seeded
`Business` ("genmars-store"), and grandfathers every current is_staff=True
account in as that business's owner.

This is an explicit, documented decision (per the approved multi-tenant
plan), not an inferred one:
  * `User.is_staff` is left completely untouched — a platform admin stays
    exactly that, independently of the BusinessMembership this migration
    also gives them. The two are unrelated facts about the same row.
  * Every is_staff=True user becomes an OWNER of this one business, because
    today there is no distinction between "runs Genmars" and "owns this
    store" — that's accurate for the system as it exists right now, but it
    is a starting point for a human to revisit (split roles, demote some to
    manager, etc.), not a judgement this migration is able to make.

Safety: after each backfill UPDATE, this migration counts remaining NULLs
and raises rather than proceeding if any row was missed — a raised
exception rolls back the whole migration (it runs inside one transaction,
Postgres's default for Django migrations), so there is no scenario where
this leaves some rows assigned and others silently not.
"""

from django.db import migrations


BUSINESS_SLUG = "genmars-store"
BUSINESS_NAME = "Genmars Store"


def backfill(apps, schema_editor):
    Business = apps.get_model("businesses", "Business")
    BusinessMembership = apps.get_model("businesses", "BusinessMembership")
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    InventoryRecord = apps.get_model("inventory", "InventoryRecord")
    Cart = apps.get_model("cart", "Cart")
    Order = apps.get_model("orders", "Order")
    StoreSettings = apps.get_model("core", "StoreSettings")
    User = apps.get_model("accounts", "User")

    business, _ = Business.objects.get_or_create(
        slug=BUSINESS_SLUG,
        defaults={
            "name": BUSINESS_NAME,
            "status": "active",
            "country": "KE",
            "timezone": "Africa/Nairobi",
            "default_currency": "KES",
        },
    )

    backfilled = {
        Category: Category.objects.filter(business__isnull=True).update(business_id=business.pk),
        Product: Product.objects.filter(business__isnull=True).update(business_id=business.pk),
        InventoryRecord: InventoryRecord.objects.filter(business__isnull=True).update(business_id=business.pk),
        Cart: Cart.objects.filter(business__isnull=True).update(business_id=business.pk),
        Order: Order.objects.filter(business__isnull=True).update(business_id=business.pk),
        StoreSettings: StoreSettings.objects.filter(business__isnull=True).update(business_id=business.pk),
    }

    # The actual safety rail: re-query rather than trust the UPDATE counts
    # above, so this catches a row that was somehow created with a non-null
    # business already pointing elsewhere just as easily as a missed one.
    remaining_nulls = {
        model._meta.label: model.objects.filter(business__isnull=True).count() for model in backfilled
    }
    unassigned = {label: count for label, count in remaining_nulls.items() if count}
    if unassigned:
        raise RuntimeError(
            f"Phase 1 backfill left rows without a business assignment: {unassigned}. "
            "Refusing to proceed — fix the data and re-run rather than continuing with "
            "tenant-unowned rows."
        )

    for user in User.objects.filter(is_staff=True):
        BusinessMembership.objects.get_or_create(
            business=business,
            user=user,
            defaults={"role": "owner", "status": "active"},
        )


def unbackfill(apps, schema_editor):
    """
    Reverse of `backfill`. Safe only while the `business` columns are still
    nullable (true for the whole span this migration is meant to run in —
    see the Phase 1 sequence: additive nullable column, this backfill, then
    a separate migration flips to non-null). Nulling these back out and
    deleting the seeded Business/memberships is the complete undo; nothing
    else was touched.
    """
    Business = apps.get_model("businesses", "Business")
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    InventoryRecord = apps.get_model("inventory", "InventoryRecord")
    Cart = apps.get_model("cart", "Cart")
    Order = apps.get_model("orders", "Order")
    StoreSettings = apps.get_model("core", "StoreSettings")

    business = Business.objects.filter(slug=BUSINESS_SLUG).first()
    if business is None:
        return

    for model in (Category, Product, InventoryRecord, Cart, Order, StoreSettings):
        model.objects.filter(business=business).update(business_id=None)

    business.memberships.all().delete()
    business.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("businesses", "0001_initial"),
        ("catalog", "0002_category_business_product_business"),
        ("cart", "0002_cart_business"),
        ("core", "0002_storesettings_business"),
        ("inventory", "0002_inventoryrecord_business"),
        ("orders", "0002_order_business"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
