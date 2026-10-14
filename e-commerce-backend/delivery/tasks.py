import logging

from celery import shared_task
from django.core.mail import send_mail

log = logging.getLogger(__name__)

SUBJECTS = {
    "paid": "We've received your payment",
    "processing": "Your order is being prepared",
    "shipped": "Your order is on its way",
    "delivered": "Your order has been delivered",
    "cancelled": "Your order was cancelled",
    "refunded": "Your order was refunded",
}


@shared_task
def send_order_status_email(order_id: int, status: str) -> None:
    from orders.models import Order

    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        return

    subject = SUBJECTS.get(status, f"Order {order.reference} update")
    send_mail(
        subject=f"{subject} — order {order.reference}",
        message=(
            f"Hi,\n\nYour order {order.reference} is now {order.get_status_display()}.\n\n"
            f"Total: {order.currency} {order.total}\n"
        ),
        from_email=None,
        recipient_list=[order.contact_email],
    )
