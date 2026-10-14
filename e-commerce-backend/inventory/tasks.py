import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task
def send_low_stock_alert(record_id: int) -> None:
    """
    Fire-and-forget — a missed alert email is not a reason to roll back the
    stock movement that triggered it, which is why this runs on_commit via
    Celery rather than inline in adjust_stock().

    MVP channel is a log line visible to ops; swap for a real email/Slack
    notification once there's a channel to send it to.
    """
    from .models import InventoryRecord

    try:
        record = InventoryRecord.objects.get(pk=record_id)
    except InventoryRecord.DoesNotExist:
        return

    log.warning(
        "LOW STOCK: %s has %s left (threshold %s)",
        record, record.quantity, record.effective_threshold,
    )
