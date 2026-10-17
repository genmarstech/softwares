"""
Phase 5 audit finding: two concurrent checkout requests against the SAME
cart could both pass create_order_from_cart()'s "cart is non-empty" read
before either one's `cart.items.all().delete()` commits, because nothing
locked the Cart (or its items) itself — only the InventoryRecord rows were
locked, which stops stock going negative but does nothing to stop the cart
being checked out twice. A real cause: a slow network causing a user to
double-tap "Place order", or a client retrying a timed-out request that
actually succeeded.

This reproduces the race directly against create_order_from_cart() (not
through the throttled HTTP view — throttling would make 10 rapid requests
flaky to drive concurrently in a test) with two separate ORM Cart instances
pointing at the same row, the same way two separate request/response cycles
each have their own Python objects. transaction=True + real threads is the
same pattern test_inventory.py already uses for its own concurrency proof.
"""

import threading

import pytest
from django.db import connection

from accounts.models import User
from businesses.services import create_business
from cart.models import Cart, CartItem
from catalog.models import Product
from inventory.models import InventoryRecord
from orders.services import OrderError, create_order_from_cart

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_on_the_same_cart_creates_only_one_order():
    """
    Enough stock exists to satisfy BOTH concurrent attempts (quantity=2 each,
    10 in stock) — if this created two orders, it would be stock-safe and
    still wrong: a customer who tapped "place order" once would see two
    orders and, if M-Pesa initiation followed, could be prompted to pay
    twice for one purchase.

    Builds its own Business/Product/Cart rather than using the
    migration-seeded `business` fixture other tests share: a
    transaction=True test flushes the whole database on teardown
    (TransactionTestCase's own behaviour, same as test_inventory.py's
    concurrency test), which can take the migration-seeded row with it for
    whichever transactional test happens to run next — self-contained
    fixtures sidestep that rather than depending on flush/reseed ordering
    between test files.
    """
    business = create_business(name="Race Test Co", slug="race-test-co")
    customer = User.objects.create_user(email="race-customer@example.com", password="a-strong-passw0rd!")
    product = Product.objects.create(
        name="Race Product", sku="RACE-SKU-1", price="500.00",
        status=Product.Status.PUBLISHED, business=business,
    )
    record = InventoryRecord.objects.create(product=product, quantity=10, business=business)
    cart = Cart.objects.create(user=customer, business=business)
    CartItem.objects.create(cart=cart, product=product, quantity=2)

    results = []
    lock = threading.Lock()

    def attempt():
        try:
            # A fresh Cart instance per thread/request, same underlying row —
            # exactly what two concurrent HTTP requests would each construct
            # via their own Cart.objects.get_or_create(...) call.
            my_cart = Cart.objects.get(pk=cart.pk)
            create_order_from_cart(customer, my_cart, **DELIVERY)
            outcome = "ok"
        except OrderError as exc:
            outcome = exc.reason
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one checkout must succeed; the other must see the cart as
    # already (correctly) emptied by its sibling, not race it into existing.
    assert results.count("ok") == 1
    assert results.count("empty_cart") == 1

    record.refresh_from_db()
    assert record.quantity == 8  # 10 - 2, deducted exactly once
