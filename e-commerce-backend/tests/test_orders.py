from decimal import Decimal

import pytest

from inventory.models import InventoryMovement
from orders.models import Order
from orders.services import OrderError, cancel_order, create_order_from_cart, transition

DELIVERY = dict(
    delivery_recipient_name="Ada Lovelace",
    delivery_line1="123 Moi Ave",
    delivery_city="Nairobi",
    delivery_phone="0712345678",
)


@pytest.mark.django_db
def test_checkout_deducts_stock_and_snapshots_price(customer, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(customer, cart, **DELIVERY)

    assert order.status == Order.Status.AWAITING_PAYMENT
    assert order.subtotal == Decimal("1000.00")  # 500.00 * 2
    assert order.items.count() == 1
    assert not cart.items.exists()  # cart cleared

    record.refresh_from_db()
    assert record.quantity == 8  # 10 - 2

    # Price changes after checkout must NOT retroactively change the order.
    product.price = Decimal("999.00")
    product.save(update_fields=["price"])
    order_item = order.items.get()
    assert order_item.unit_price == Decimal("500.00")


@pytest.mark.django_db
def test_checkout_rejects_insufficient_stock(customer, cart_with_item):
    cart, product, record = cart_with_item
    record.quantity = 1
    record.save(update_fields=["quantity"])

    with pytest.raises(OrderError) as exc_info:
        create_order_from_cart(customer, cart, **DELIVERY)
    assert exc_info.value.reason == "insufficient_stock"

    # Nothing partially applied — the whole checkout rolled back.
    assert cart.items.exists()
    record.refresh_from_db()
    assert record.quantity == 1


@pytest.mark.django_db
def test_checkout_rejects_unpublished_product(customer, cart_with_item):
    cart, product, record = cart_with_item
    product.status = product.Status.DRAFT
    product.save(update_fields=["status"])

    with pytest.raises(OrderError) as exc_info:
        create_order_from_cart(customer, cart, **DELIVERY)
    assert exc_info.value.reason == "product_unavailable"


@pytest.mark.django_db
def test_checkout_rejects_empty_cart(customer):
    from cart.models import Cart

    cart = Cart.objects.create(user=customer)
    with pytest.raises(OrderError) as exc_info:
        create_order_from_cart(customer, cart, **DELIVERY)
    assert exc_info.value.reason == "empty_cart"


@pytest.mark.django_db
def test_only_allowed_status_transitions_are_permitted(customer, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(customer, cart, **DELIVERY)

    # awaiting_payment -> paid is allowed
    order = transition(order, Order.Status.PAID, actor=customer)
    assert order.status == Order.Status.PAID

    # paid -> awaiting_payment is not
    with pytest.raises(OrderError) as exc_info:
        transition(order, Order.Status.AWAITING_PAYMENT, actor=customer)
    assert exc_info.value.reason == "invalid_transition"

    history = list(order.status_history.values_list("to_status", flat=True))
    assert history == [Order.Status.AWAITING_PAYMENT, Order.Status.PAID]


@pytest.mark.django_db
def test_cancelling_an_order_restores_stock_exactly_once(customer, cart_with_item):
    cart, product, record = cart_with_item
    order = create_order_from_cart(customer, cart, **DELIVERY)
    record.refresh_from_db()
    assert record.quantity == 8

    order = cancel_order(order, actor=customer, reason="changed my mind")
    record.refresh_from_db()
    assert record.quantity == 10
    assert order.stock_restored is True

    restore_movements = record.movements.filter(reason=InventoryMovement.Reason.CANCELLATION)
    assert restore_movements.count() == 1

    # Cancelled is terminal — cannot cancel again, so stock can't double-restore.
    with pytest.raises(OrderError):
        cancel_order(order, actor=customer)
    record.refresh_from_db()
    assert record.quantity == 10
