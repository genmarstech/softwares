"""
Seeds a believable general-store catalog for local/demo use — categories,
products, one variant example, and starting stock. Idempotent: re-running
it never duplicates a category or product (matched by slug/SKU), and never
resets stock on a product that already has an InventoryRecord, so it's safe
to run again after the admin has made real adjustments.

Not meant for production data — there's no real supplier/pricing feed
behind this, it's demo content so the storefront and admin dashboard don't
look empty.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Category, Product, ProductVariant
from inventory.models import InventoryRecord

CATEGORIES = [
    "Electronics",
    "Home & Kitchen",
    "Fashion",
    "Beauty & Personal Care",
    "Sports & Outdoors",
    "Books & Stationery",
]

# (name, sku, price, category, description, stock)
PRODUCTS = [
    ("Wireless Earbuds Pro", "EL-001", "3500.00", "Electronics",
     "True wireless earbuds with active noise cancellation and a 24-hour charging case.", 40),
    ("Bluetooth Speaker Mini", "EL-002", "2800.00", "Electronics",
     "Pocket-sized speaker with punchy bass and 10 hours of playback on a single charge.", 25),
    ("USB-C Fast Charger 20W", "EL-003", "1200.00", "Electronics",
     "Compact 20W USB-C power adapter, compatible with most phones and earbud cases.", 60),
    ("Power Bank 10000mAh", "EL-004", "2200.00", "Electronics",
     "Slim 10000mAh power bank with dual USB output for charging two devices at once.", 3),
    ("Smart LED Desk Lamp", "EL-005", "1800.00", "Electronics",
     "Touch-dimmable LED desk lamp with three brightness levels and a USB charging port.", 15),
    ("Non-Stick Frying Pan 28cm", "HK-001", "1500.00", "Home & Kitchen",
     "Durable non-stick frying pan with a heat-resistant handle, suitable for all stovetops.", 20),
    ("Electric Kettle 1.7L", "HK-002", "2300.00", "Home & Kitchen",
     "Rapid-boil 1.7L electric kettle with auto shut-off and a concealed heating element.", 18),
    ("Ceramic Dinner Set (16pc)", "HK-003", "4500.00", "Home & Kitchen",
     "16-piece ceramic dinner set for four — plates, bowls, and side plates included.", 10),
    ("Stainless Steel Water Bottle", "HK-004", "800.00", "Home & Kitchen",
     "Double-walled stainless steel bottle that keeps drinks cold for 24 hours.", 50),
    ("Cotton Bed Sheet Set (Queen)", "HK-005", "2600.00", "Home & Kitchen",
     "100% cotton queen-size sheet set — fitted sheet, flat sheet, and two pillowcases.", 2),
    ("Unisex Canvas Sneakers", "FA-003", "3200.00", "Fashion",
     "Everyday canvas sneakers with a cushioned insole, available in classic colourways.", 18),
    ("Leather Wallet", "FA-004", "1500.00", "Fashion",
     "Genuine leather bifold wallet with six card slots and a coin pocket.", 35),
    ("Shea Butter Body Lotion 400ml", "BE-001", "950.00", "Beauty & Personal Care",
     "Rich, fast-absorbing body lotion made with unrefined shea butter.", 45),
    ("Organic Face Wash 150ml", "BE-002", "750.00", "Beauty & Personal Care",
     "Gentle daily face wash with aloe vera, suitable for sensitive skin.", 40),
    ("Hair Growth Oil 100ml", "BE-003", "1100.00", "Beauty & Personal Care",
     "Lightweight blend of castor and rosemary oil for scalp care.", 5),
    ("Yoga Mat Premium", "SP-001", "1700.00", "Sports & Outdoors",
     "6mm extra-cushioned yoga mat with a non-slip textured surface.", 25),
    ("Adjustable Dumbbell Set", "SP-002", "5500.00", "Sports & Outdoors",
     "Pair of adjustable dumbbells, 2–20kg each, for home strength training.", 8),
    ("Camping Tent (2-Person)", "SP-003", "6800.00", "Sports & Outdoors",
     "Lightweight 2-person tent with a waterproof rainfly, packs down to 45cm.", 6),
    ("A5 Hardcover Notebook (3-pack)", "BS-001", "650.00", "Books & Stationery",
     "Set of three A5 hardcover notebooks, 120 dotted pages each.", 60),
    ("Gel Pen Set (12 colors)", "BS-002", "450.00", "Books & Stationery",
     "12 vibrant gel pens in a reusable case — smooth write, quick-dry ink.", 70),
]

# A product sold as explicit variants — has no inventory record of its own;
# each variant gets one instead (InventoryRecord allows exactly one of
# product/variant, never both — see inventory/models.py).
SHIRT_VARIANTS = [
    ("Size", "S", "FA-001-S", 10),
    ("Size", "M", "FA-001-M", 18),
    ("Size", "L", "FA-001-L", 14),
    ("Size", "XL", "FA-001-XL", 6),
]


class Command(BaseCommand):
    help = "Seeds demo categories, products, and starting stock."

    @transaction.atomic
    def handle(self, *args, **options):
        categories = {}
        for name in CATEGORIES:
            category, created = Category.objects.get_or_create(name=name)
            categories[name] = category
            self.stdout.write(f"{'created' if created else 'exists'}  category  {name}")

        for name, sku, price, category_name, description, stock in PRODUCTS:
            product, created = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "price": price,
                    "description": description,
                    "status": Product.Status.PUBLISHED,
                    "category": categories[category_name],
                },
            )
            if not created:
                product.name = name
                product.price = price
                product.description = description
                product.status = Product.Status.PUBLISHED
                product.category = categories[category_name]
                product.save()

            InventoryRecord.objects.get_or_create(product=product, defaults={"quantity": stock})
            self.stdout.write(f"{'created' if created else 'updated'}  product   {sku}  {name}")

        shirt, shirt_created = Product.objects.get_or_create(
            sku="FA-001",
            defaults={
                "name": "Men's Classic Fit Shirt",
                "price": "1800.00",
                "description": "Breathable cotton-blend shirt, classic fit, machine washable.",
                "status": Product.Status.PUBLISHED,
                "category": categories["Fashion"],
            },
        )
        self.stdout.write(f"{'created' if shirt_created else 'exists'}  product   FA-001  {shirt.name}")

        for variant_name, value, variant_sku, stock in SHIRT_VARIANTS:
            variant, created = ProductVariant.objects.get_or_create(
                sku=variant_sku,
                defaults={"product": shirt, "name": variant_name, "value": value},
            )
            InventoryRecord.objects.get_or_create(variant=variant, defaults={"quantity": stock})
            self.stdout.write(f"{'created' if created else 'exists'}  variant   {variant_sku}")

        self.stdout.write(self.style.SUCCESS("Catalog seed complete."))
