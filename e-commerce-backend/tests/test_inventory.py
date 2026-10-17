import threading

import pytest
from django.db import connection

from businesses.services import create_business
from catalog.models import Product
from inventory.models import InventoryMovement, InventoryRecord
from inventory.services import InsufficientStock, adjust_stock


@pytest.mark.django_db
def test_adjust_stock_refuses_to_go_negative(product_with_stock):
    _, record = product_with_stock(quantity=3)
    with pytest.raises(InsufficientStock):
        adjust_stock(record, -4, reason=InventoryMovement.Reason.ORDER)
    record.refresh_from_db()
    assert record.quantity == 3  # unchanged — the attempt left no trace on quantity


@pytest.mark.django_db
def test_adjust_stock_writes_an_append_only_movement(product_with_stock):
    _, record = product_with_stock(quantity=5)
    adjust_stock(record, -2, reason=InventoryMovement.Reason.ORDER, reference="GM-TEST")
    record.refresh_from_db()
    assert record.quantity == 3
    movement = record.movements.get()
    assert movement.delta == -2
    assert movement.resulting_quantity == 3
    assert movement.reference == "GM-TEST"


@pytest.mark.django_db(transaction=True)
def test_concurrent_decrements_never_oversell():
    """
    Ten threads race to take the last 5 units. Exactly 5 must succeed and 5
    must fail with InsufficientStock — never more sold than existed, and the
    final quantity must never go negative. This is the checklist's own
    "test concurrent purchases" requirement, exercised directly against
    adjust_stock's select_for_update locking.

    Builds its own Business/Product rather than depending on the
    migration-seeded `business` fixture via `product_with_stock`: a
    transaction=True test flushes the whole database on teardown, which can
    take that migration-seeded row with it for whichever transactional test
    runs next in the same session (confirmed — this test and
    test_checkout_race.py's concurrency test used to fail each other
    depending on file collection order before both became self-contained).
    """
    business = create_business(name="Inventory Test Co", slug="inventory-test-co")
    product = Product.objects.create(
        name="Race Stock Product", sku="INV-RACE-SKU", price="500.00",
        status=Product.Status.PUBLISHED, business=business,
    )
    record = InventoryRecord.objects.create(product=product, quantity=5, business=business)

    results = []
    lock = threading.Lock()

    def attempt():
        try:
            adjust_stock(record, -1, reason=InventoryMovement.Reason.ORDER)
            outcome = "ok"
        except InsufficientStock:
            outcome = "rejected"
        finally:
            connection.close()  # each thread gets its own DB connection
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    record.refresh_from_db()
    assert results.count("ok") == 5
    assert results.count("rejected") == 5
    assert record.quantity == 0
