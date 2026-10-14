"""
Backfills ProductVariant.business from its own product — not from the
Phase 1 "seeded business" bridge, deliberately: a variant's correct
business is always whatever its parent product's business already is, by
definition, so deriving it from that relationship is strictly correct
rather than an assumption that happens to hold today. Confirmed on real
data before writing this: zero cross-business SKU collisions existed at
the time (checked via a one-off query against every ProductVariant,
grouping by sku across businesses).

Same safety rail as every other Phase 1 backfill: re-checks for any
remaining NULL after the update and raises rather than proceeding if the
loop below somehow missed one.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    ProductVariant = apps.get_model("catalog", "ProductVariant")

    for variant in ProductVariant.objects.filter(business__isnull=True).select_related("product"):
        variant.business_id = variant.product.business_id
        variant.save(update_fields=["business"])

    remaining = ProductVariant.objects.filter(business__isnull=True).count()
    if remaining:
        raise RuntimeError(
            f"{remaining} ProductVariant row(s) still have no business after backfill — "
            "refusing to proceed."
        )


def unbackfill(apps, schema_editor):
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    ProductVariant.objects.update(business=None)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_productvariant_business_alter_productvariant_sku_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
