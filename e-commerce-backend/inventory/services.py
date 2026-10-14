"""
The one place `InventoryRecord.quantity` is ever written.

Every caller — order creation, cancellation, admin restock, returns — goes
through `adjust_stock()`. It locks the row, refuses to let quantity go
negative, and writes an append-only `InventoryMovement` for every change, so
"what happened to this product's stock" is always answerable from the
movement log rather than trusted to whoever last edited the row.
"""

from __future__ import annotations

from django.db import transaction

from .models import InventoryMovement, InventoryRecord


class InsufficientStock(Exception):
    def __init__(self, record: InventoryRecord, requested: int):
        self.record = record
        self.requested = requested
        super().__init__(
            f"Only {record.quantity} left of {record}; cannot remove {-requested if requested < 0 else requested}."
        )


def adjust_stock(
    record: InventoryRecord,
    delta: int,
    *,
    reason: str,
    actor=None,
    reference: str = "",
    note: str = "",
) -> InventoryRecord:
    """
    Apply `delta` (positive to add, negative to remove) to `record.quantity`
    inside a transaction, locking the row first so two concurrent calls can
    never both read the same starting quantity. Raises `InsufficientStock`
    before the row would go negative — the caller's own transaction then
    rolls back, so no partial order/payment is left behind.
    """
    with transaction.atomic():
        locked = InventoryRecord.objects.select_for_update().get(pk=record.pk)
        new_quantity = locked.quantity + delta
        if new_quantity < 0:
            raise InsufficientStock(locked, delta)

        locked.quantity = new_quantity
        locked.save(update_fields=["quantity"])

        InventoryMovement.objects.create(
            record=locked,
            delta=delta,
            resulting_quantity=new_quantity,
            reason=reason,
            reference=reference,
            note=note,
            actor=actor,
        )

        if locked.is_low_stock:
            from .tasks import send_low_stock_alert

            transaction.on_commit(lambda: send_low_stock_alert.delay(locked.pk))

    return locked
