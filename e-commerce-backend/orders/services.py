"""
Order creation and status transitions — the two places the checklist's
non-negotiable rules actually get enforced:

  * "Backend owns the price" — create_order_from_cart() re-reads price and
    availability from Product/ProductVariant at the moment of checkout. The
    cart's cached unit_price (cart/models.py) is display-only and is never
    read here.
  * "Protect stock" — every item is locked (via inventory.services.adjust_stock,
    which itself uses select_for_update) inside one transaction with order
    creation, so two concurrent checkouts over the same remaining stock
    cannot both succeed.
  * "Preserve purchase history" — OrderItem rows are a snapshot; nothing
    about them is re-derived from Product after creation.

Stock is deducted HERE, at order creation — not reserved earlier at
add-to-cart and not deferred until payment succeeds. That keeps the cart
simple (nothing to expire) at the cost of a published-but-unpaid order being
able to hold stock while `awaiting_payment`. Cancelling or refunding an order
restores it — see `transition()`.
"""

from __future__ import annotations

from django.db import transaction

from catalog.models import Product, ProductVariant
from cart.models import Cart
from inventory.models import InventoryMovement, InventoryRecord
from inventory.services import InsufficientStock, adjust_stock

from .models import Order, OrderItem, OrderStatusHistory


class OrderError(Exception):
    """Raised for any reason an order cannot be created or transitioned.
    `safe_message` is fine to show the customer directly."""

    def __init__(self, reason: str, safe_message: str):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = safe_message


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    Order.Status.AWAITING_PAYMENT: {Order.Status.PAID, Order.Status.PAYMENT_FAILED, Order.Status.CANCELLED},
    Order.Status.PAYMENT_FAILED: {Order.Status.AWAITING_PAYMENT, Order.Status.CANCELLED},
    Order.Status.PAID: {Order.Status.PROCESSING, Order.Status.CANCELLED, Order.Status.REFUNDED},
    Order.Status.PROCESSING: {Order.Status.SHIPPED, Order.Status.CANCELLED, Order.Status.REFUNDED},
    Order.Status.SHIPPED: {Order.Status.DELIVERED, Order.Status.REFUNDED},
    Order.Status.DELIVERED: {Order.Status.REFUNDED},
    Order.Status.CANCELLED: set(),
    Order.Status.REFUNDED: set(),
}

# Entering either of these restores the stock the order was holding.
STOCK_RESTORING_STATUSES = {Order.Status.CANCELLED, Order.Status.REFUNDED}

# Statuses whose customer should hear about the change by email.
NOTIFY_STATUSES = {
    Order.Status.PAID,
    Order.Status.PROCESSING,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
    Order.Status.CANCELLED,
    Order.Status.REFUNDED,
}


def _record_for(product: Product, variant: ProductVariant | None) -> InventoryRecord:
    try:
        return InventoryRecord.objects.get(product=product) if variant is None else InventoryRecord.objects.get(variant=variant)
    except InventoryRecord.DoesNotExist as exc:
        raise OrderError("no_inventory_record", f"{product.name} is not available right now.") from exc


@transaction.atomic
def create_order_from_cart(
    user,
    cart: Cart,
    *,
    delivery_recipient_name: str,
    delivery_line1: str,
    delivery_city: str,
    delivery_phone: str,
    delivery_line2: str = "",
    delivery_fee=0,
    contact_phone: str = "",
    business=None,
) -> Order:
    # `business` defaults to the cart's own — a cart is already resolved per
    # business (cart/views.py), so the normal checkout path never needs to
    # pass this explicitly. It exists as a parameter so a caller can still
    # assert which business it expects, catching a mismatch here rather
    # than silently trusting the cart.
    if business is not None and business.pk != cart.business_id:
        raise OrderError("business_mismatch", "This cart does not belong to that business.")
    business = business or cart.business

    items = list(cart.items.select_related("product", "variant").order_by("product_id"))
    if not items:
        raise OrderError("empty_cart", "Your cart is empty.")

    # Lock every record this checkout touches, in a stable (pk) order, before
    # reading or writing any of them — the standard way to avoid two
    # concurrent checkouts deadlocking each other on the opposite order.
    records = {}
    for item in items:
        record = _record_for(item.product, item.variant)
        records[item.id] = InventoryRecord.objects.select_for_update().get(pk=record.pk)

    order_items_data = []
    subtotal = 0
    for item in items:
        # Re-read price/status fresh rather than trusting the cart item's
        # cached FK — this is the "backend owns the price" line in practice.
        product = Product.objects.get(pk=item.product_id)
        if product.status != Product.Status.PUBLISHED:
            raise OrderError("product_unavailable", f"{product.name} is no longer available.")

        variant = item.variant
        unit_price = variant.price if variant else product.price
        line_total = unit_price * item.quantity
        subtotal += line_total

        order_items_data.append(
            {
                "product": product,
                "variant": variant,
                "product_name": product.name if not variant else f"{product.name} ({variant.value})",
                "sku": variant.sku if variant else product.sku,
                "unit_price": unit_price,
                "quantity": item.quantity,
                "line_total": line_total,
                "record": records[item.id],
            }
        )

    total = subtotal + delivery_fee

    order = Order.objects.create(
        customer=user,
        business=business,
        status=Order.Status.AWAITING_PAYMENT,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        contact_email=user.email,
        contact_phone=contact_phone or user.phone_number,
        delivery_recipient_name=delivery_recipient_name,
        delivery_line1=delivery_line1,
        delivery_line2=delivery_line2,
        delivery_city=delivery_city,
        delivery_phone=delivery_phone,
    )

    for data in order_items_data:
        OrderItem.objects.create(
            order=order,
            product=data["product"],
            variant=data["variant"],
            product_name=data["product_name"],
            sku=data["sku"],
            unit_price=data["unit_price"],
            quantity=data["quantity"],
            line_total=data["line_total"],
        )
        try:
            adjust_stock(
                data["record"],
                -data["quantity"],
                reason=InventoryMovement.Reason.ORDER,
                actor=user,
                reference=order.reference,
            )
        except InsufficientStock as exc:
            raise OrderError(
                "insufficient_stock", f"Only {exc.record.quantity} of {data['product_name']} left."
            ) from exc

    OrderStatusHistory.objects.create(
        order=order, from_status="", to_status=Order.Status.AWAITING_PAYMENT, actor=user, note="Order created"
    )

    cart.items.all().delete()
    return order


@transaction.atomic
def transition(order: Order, to_status: str, *, actor=None, note: str = "") -> Order:
    """The only path that changes Order.status. Always logs, never skips a check."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    allowed = ALLOWED_TRANSITIONS.get(locked.status, set())
    if to_status not in allowed:
        raise OrderError(
            "invalid_transition", f"Cannot move an order from {locked.status} to {to_status}."
        )

    from_status = locked.status
    locked.status = to_status

    if to_status in STOCK_RESTORING_STATUSES and not locked.stock_restored:
        reason = (
            InventoryMovement.Reason.CANCELLATION
            if to_status == Order.Status.CANCELLED
            else InventoryMovement.Reason.REFUND
        )
        for item in locked.items.select_related("product", "variant").all():
            if item.product is None and item.variant is None:
                continue  # product/variant was deleted since; nothing to restock
            try:
                record = _record_for(item.product, item.variant)
            except OrderError:
                continue
            adjust_stock(record, item.quantity, reason=reason, actor=actor, reference=locked.reference)
        locked.stock_restored = True

    locked.save(update_fields=["status", "stock_restored"])
    OrderStatusHistory.objects.create(
        order=locked, from_status=from_status, to_status=to_status, actor=actor, note=note
    )

    if to_status in NOTIFY_STATUSES:
        transaction.on_commit(lambda: _notify(locked, to_status))

    return locked


def cancel_order(order: Order, *, actor=None, reason: str = "") -> Order:
    return transition(order, Order.Status.CANCELLED, actor=actor, note=reason)


def _notify(order: Order, status: str) -> None:
    from delivery.tasks import send_order_status_email

    send_order_status_email.delay(order.pk, status)
