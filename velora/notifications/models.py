from django.db import models
from django.utils import timezone

from business.models import Business


class Notification(models.Model):
    """One message to one recipient on one channel, with its delivery history."""

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        TELEGRAM = 'telegram', 'Telegram'
        PUSH = 'push', 'Push'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        SENT = 'sent', 'Sent'
        DELIVERED = 'delivered', 'Delivered'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'
        CANCELLED = 'cancelled', 'Cancelled'

    class Event(models.TextChoices):
        BOOKING_CREATED = 'booking_created', 'Booking received'
        BOOKING_CONFIRMED = 'booking_confirmed', 'Booking confirmed'
        BOOKING_RESCHEDULED = 'booking_rescheduled', 'Booking rescheduled'
        BOOKING_CANCELLED = 'booking_cancelled', 'Booking cancelled'
        BOOKING_NO_SHOW = 'booking_no_show', 'Missed appointment'
        HOLD_EXPIRED = 'hold_expired', 'Hold expired'
        PAYMENT_RECEIPT = 'payment_receipt', 'Payment receipt'
        REMINDER = 'reminder', 'Reminder'
        FOLLOW_UP = 'follow_up', 'Post-visit follow-up'
        WAITLIST_OPENING = 'waitlist_opening', 'Waitlist opening'
        STAFF_NEW_BOOKING = 'staff_new_booking', 'Staff: new booking'
        STAFF_BOOKING_CHANGED = 'staff_booking_changed', 'Staff: booking changed'
        STAFF_WALK_IN = 'staff_walk_in', 'Staff: walk-in added'
        SUMMARY_REPORT = 'summary_report', 'Summary report'

    class Recipient(models.TextChoices):
        CUSTOMER = 'customer', 'Customer'
        STAFF = 'staff', 'Staff'
        BUSINESS = 'business', 'Business'

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='notifications')
    booking = models.ForeignKey(
        'bookings.Booking', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications'
    )
    event = models.CharField(max_length=40, choices=Event.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    recipient_type = models.CharField(max_length=20, choices=Recipient.choices, default=Recipient.CUSTOMER)
    recipient_name = models.CharField(max_length=255, blank=True, default='')
    to_address = models.CharField(max_length=255)
    subject = models.CharField(max_length=255, blank=True, default='')
    body = models.TextField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    scheduled_for = models.DateTimeField(default=timezone.now)
    respect_quiet_hours = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=40, blank=True, default='')
    provider_message_id = models.CharField(max_length=255, blank=True, default='')
    error = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'scheduled_for']),
            models.Index(fields=['business', 'created_at']),
        ]

    def __str__(self):
        return f'{self.get_event_display()} via {self.channel} to {self.to_address} [{self.status}]'
