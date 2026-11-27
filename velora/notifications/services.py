"""Event-driven notifications for bookings.

Booking code calls the event functions below (``booking_created`` and so on).
Each one writes ``Notification`` rows, one per recipient and channel, and sends
the ones that are due straight away. Scheduled rows (reminders, follow-ups,
anything deferred by quiet hours, retries) are sent by the
``process_notifications`` management command, which should run every minute
from cron or a systemd timer.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Notification
from .providers import ProviderError, get_provider

RETRY_DELAYS_MINUTES = [5, 15, 45]
CUSTOMER_CHANNELS = ('email', 'sms', 'whatsapp', 'push')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy(business):
    from bookings.services import get_policy
    return get_policy(business)


def _tz(business):
    from bookings.services import business_tz
    return business_tz(_policy(business))


def manage_link(booking):
    return f'{settings.FRONTEND_URL}/manage/{booking.manage_token}'


def booking_page_link(business):
    return f'{settings.FRONTEND_URL}/book/{business.slug or business.id}'


def _when(booking, start=None):
    local = (start or booking.start).astimezone(_tz(booking.business))
    return local.strftime('%a %d %b %Y at %H:%M')


def _address_for(customer, channel):
    if channel == 'email':
        return customer.email
    if channel in ('sms', 'whatsapp'):
        return customer.phone_number
    return ''  # push needs a device token, which arrives with the mobile app


def customer_channels(business, customer):
    """Channels this customer can be reached on, honouring opt-outs."""
    configured = [c for c in _policy(business).notification_channels if c in CUSTOMER_CHANNELS]
    if customer.preferred_channel and customer.preferred_channel not in configured:
        configured.append(customer.preferred_channel)
    result = []
    for channel in configured:
        if customer.opted_out(channel):
            continue
        if not _address_for(customer, channel):
            continue
        result.append(channel)
    return result


def apply_quiet_hours(business, when):
    """Move ``when`` to the end of quiet hours if it falls inside them."""
    policy = _policy(business)
    if not policy.quiet_hours_start or not policy.quiet_hours_end:
        return when
    tz = _tz(business)
    local = when.astimezone(tz)
    start, end = policy.quiet_hours_start, policy.quiet_hours_end
    t = local.time()
    if start <= end:
        inside = start <= t < end
        resume_day = local.date()
    else:  # overnight, e.g. 21:00 to 07:00
        inside = t >= start or t < end
        resume_day = local.date() + timedelta(days=1) if t >= start else local.date()
    if not inside:
        return when
    return datetime.combine(resume_day, end, tzinfo=tz)


def _reply_keywords(channel):
    # Reply keywords only make sense once an SMS/WhatsApp provider forwards
    # replies to Velora, so they stay off until one is connected.
    if getattr(settings, 'VELORA_REPLY_KEYWORDS_ENABLED', False) and channel in ('sms', 'whatsapp'):
        return '\nReply C to confirm, R to reschedule or X to cancel.'
    return ''


def queue(*, business, event, channel, to_address, body, subject='', booking=None, recipient_type='customer',
          recipient_name='', scheduled_for=None, respect_quiet_hours=False):
    if not to_address:
        return None
    scheduled_for = scheduled_for or timezone.now()
    if respect_quiet_hours:
        scheduled_for = apply_quiet_hours(business, scheduled_for)
    status = Notification.Status.QUEUED
    error = ''
    if channel == 'push':
        status, error = Notification.Status.SKIPPED, 'Push notifications need the mobile app.'
    return Notification.objects.create(
        business=business, booking=booking, event=event, channel=channel, to_address=to_address,
        subject=subject, body=body, recipient_type=recipient_type, recipient_name=recipient_name,
        scheduled_for=scheduled_for, respect_quiet_hours=respect_quiet_hours, status=status, error=error,
    )


def notify_customer(booking, event, subject, message, *, scheduled_for=None, respect_quiet_hours=False,
                    with_actions=True):
    business, customer = booking.business, booking.customer
    created = []
    for channel in customer_channels(business, customer):
        body = f'Hi {customer.full_name.split(" ")[0]}, {message}'
        if with_actions:
            body += f'\nManage your booking: {manage_link(booking)}'
            body += _reply_keywords(channel)
        body += f'\n— {business.business_name}'
        n = queue(
            business=business, booking=booking, event=event, channel=channel,
            to_address=_address_for(customer, channel), subject=f'{subject} · {business.business_name}',
            body=body, recipient_name=customer.full_name, scheduled_for=scheduled_for,
            respect_quiet_hours=respect_quiet_hours,
        )
        if n:
            created.append(n)
    return created


def notify_team(booking, event, message):
    """Alert the assigned staff member and the business inbox."""
    business = booking.business
    if not _policy(business).notify_staff:
        return []
    created = []
    recipients = []
    if booking.staff and booking.staff.staff_email:
        recipients.append(('staff', booking.staff.staff_name, booking.staff.staff_email))
    if business.business_email and business.business_email not in [r[2] for r in recipients]:
        recipients.append(('business', business.business_name, business.business_email))
    for recipient_type, name, email in recipients:
        created.append(queue(
            business=business, booking=booking, event=event, channel='email', to_address=email,
            subject=f'{Notification.Event(event).label}: {booking.customer.full_name}',
            body=message, recipient_type=recipient_type, recipient_name=name,
        ))
    return created


def _summary(booking):
    staff = f' with {booking.staff.staff_name}' if booking.staff else ''
    return f'{booking.service.service_name}{staff} on {_when(booking)}'


def _customer_card(booking):
    """Who booked and how to reach them, for messages to the business."""
    c = booking.customer
    lines = [
        f'Customer: {c.full_name}',
        f'Phone: {c.phone_number or "not given"}',
        f'Email: {c.email or "not given"}',
        f'Service: {booking.service.service_name}' + (f' with {booking.staff.staff_name}' if booking.staff else ''),
        f'When: {_when(booking)}',
        f'Status: {booking.get_status_display()}',
    ]
    if c.first_channel:
        lines.append(f'First booked via: {c.first_channel}')
    lines.append(f'Notes: {booking.intake_notes or "none"}')
    lines.append(f'Open in Velora: {settings.FRONTEND_URL}/dashboard/{booking.business_id}/bookings?booking={booking.id}')
    return '\n'.join(lines)


def cancel_scheduled(booking, events=(Notification.Event.REMINDER, Notification.Event.FOLLOW_UP)):
    Notification.objects.filter(
        booking=booking, event__in=events, status=Notification.Status.QUEUED,
    ).update(status=Notification.Status.CANCELLED, error='Booking changed')


def schedule_reminders(booking):
    cancel_scheduled(booking, events=(Notification.Event.REMINDER,))
    now = timezone.now()
    for hours in sorted(set(_policy(booking.business).reminder_hours), reverse=True):
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            continue
        send_at = booking.start - timedelta(hours=hours)
        if send_at <= now:
            continue
        label = f'{hours:g} hour' + ('' if hours == 1 else 's')
        notify_customer(
            booking, Notification.Event.REMINDER, 'Appointment reminder',
            f'a reminder of your appointment in {label}: {_summary(booking)}.',
            scheduled_for=send_at, respect_quiet_hours=False,
        )


def dispatch_due(booking=None, limit=200):
    """Send queued notifications that are due. Returns how many were attempted."""
    now = timezone.now()
    qs = Notification.objects.filter(status=Notification.Status.QUEUED, scheduled_for__lte=now)
    if booking is not None:
        qs = qs.filter(booking=booking)
    qs = qs.exclude(next_attempt_at__gt=now)
    count = 0
    for notification in qs.order_by('scheduled_for')[:limit]:
        send(notification)
        count += 1
    return count


def send(notification):
    max_attempts = getattr(settings, 'VELORA_NOTIFICATION_MAX_ATTEMPTS', 3)
    with transaction.atomic():
        n = Notification.objects.select_for_update().get(pk=notification.pk)
        if n.status != Notification.Status.QUEUED:
            return n
        # Quiet hours are checked again at send time, in case the policy changed.
        if n.respect_quiet_hours:
            resume = apply_quiet_hours(n.business, timezone.now())
            if resume > timezone.now():
                n.scheduled_for = resume
                n.save(update_fields=['scheduled_for'])
                return n
        n.attempts += 1
        try:
            provider = get_provider(n.channel)
            n.provider = provider.name
            n.provider_message_id = provider.send(n) or ''
            n.status = Notification.Status.SENT
            n.sent_at = timezone.now()
            n.error = ''
            n.next_attempt_at = None
        except (ProviderError, Exception) as exc:  # noqa: BLE001 - any provider failure is retried
            n.error = str(exc)[:2000]
            if n.attempts >= max_attempts:
                n.status = Notification.Status.FAILED
                n.next_attempt_at = None
            else:
                delay = RETRY_DELAYS_MINUTES[min(n.attempts - 1, len(RETRY_DELAYS_MINUTES) - 1)]
                n.next_attempt_at = timezone.now() + timedelta(minutes=delay)
        n.save()
        return n


def _send_now(booking):
    dispatch_due(booking=booking)


# ---------------------------------------------------------------------------
# Booking lifecycle events
# ---------------------------------------------------------------------------

def booking_created(booking):
    status = booking.status
    if status == 'held':
        notify_customer(
            booking, Notification.Event.BOOKING_CREATED, 'Booking on hold',
            f'we are holding {_summary(booking)} for you. Please pay the deposit of '
            f'{booking.deposit_amount} to confirm it.',
        )
    elif status == 'pending':
        notify_customer(
            booking, Notification.Event.BOOKING_CREATED, 'Booking received',
            f'we received your request for {_summary(booking)}. We will confirm it shortly.',
        )
    else:
        notify_customer(
            booking, Notification.Event.BOOKING_CONFIRMED, 'Booking confirmed',
            f'your booking is confirmed: {_summary(booking)}.',
        )
        schedule_reminders(booking)

    if booking.channel == 'walk_in':
        notify_team(booking, Notification.Event.STAFF_WALK_IN,
                    f'Walk-in added: {booking.customer.full_name} for {_summary(booking)}.')
    else:
        notify_team(booking, Notification.Event.STAFF_NEW_BOOKING,
                    f'New booking: {booking.customer.full_name} for {_summary(booking)}.\n\n'
                    f'{_customer_card(booking)}')
    _send_now(booking)


def booking_confirmed(booking):
    notify_customer(booking, Notification.Event.BOOKING_CONFIRMED, 'Booking confirmed',
                    f'your booking is confirmed: {_summary(booking)}.')
    schedule_reminders(booking)
    _send_now(booking)


def booking_rescheduled(booking, previous_start):
    notify_customer(booking, Notification.Event.BOOKING_RESCHEDULED, 'Booking moved',
                    f'your booking has moved from {_when(booking, previous_start)} to {_when(booking)}: '
                    f'{_summary(booking)}.')
    notify_team(booking, Notification.Event.STAFF_BOOKING_CHANGED,
                f'{booking.customer.full_name} moved {booking.service.service_name} from '
                f'{_when(booking, previous_start)} to {_when(booking)}.')
    if booking.status != 'held':
        schedule_reminders(booking)
    _send_now(booking)


def booking_cancelled(booking):
    cancel_scheduled(booking)
    fee = f' A late cancellation fee of {booking.cancellation_fee} applies.' if booking.cancellation_fee else ''
    notify_customer(booking, Notification.Event.BOOKING_CANCELLED, 'Booking cancelled',
                    f'your booking for {_summary(booking)} has been cancelled.{fee} '
                    f'Book again any time: {booking_page_link(booking.business)}', with_actions=False)
    notify_team(booking, Notification.Event.STAFF_BOOKING_CHANGED,
                f'Cancelled by {booking.cancelled_by or "unknown"}: {booking.customer.full_name}, '
                f'{_summary(booking)}. Reason: {booking.cancellation_reason or "none given"}.')
    _send_now(booking)


def hold_expired(booking):
    cancel_scheduled(booking)
    notify_customer(booking, Notification.Event.HOLD_EXPIRED, 'Booking released',
                    f'we did not receive the deposit for {_summary(booking)}, so the slot has been released. '
                    f'You can book again here: {booking_page_link(booking.business)}', with_actions=False)
    _send_now(booking)


def booking_completed(booking):
    cancel_scheduled(booking, events=(Notification.Event.REMINDER,))
    hours = _policy(booking.business).follow_up_hours
    notify_customer(
        booking, Notification.Event.FOLLOW_UP, 'Thank you for visiting',
        f'thank you for visiting us for {booking.service.service_name}. We would love your feedback. '
        f'Ready for the next one? {booking_page_link(booking.business)}',
        scheduled_for=timezone.now() + timedelta(hours=hours), respect_quiet_hours=True, with_actions=False,
    )
    _send_now(booking)


def booking_no_show(booking):
    cancel_scheduled(booking)
    notify_customer(booking, Notification.Event.BOOKING_NO_SHOW, 'We missed you',
                    f'we missed you for {_summary(booking)}. Rebook here: {booking_page_link(booking.business)}',
                    respect_quiet_hours=True, with_actions=False)
    _send_now(booking)


def payment_receipt(payment):
    booking = payment.booking
    notify_customer(booking, Notification.Event.PAYMENT_RECEIPT, 'Payment receipt',
                    f'we received {payment.amount} ({payment.get_kind_display().lower()}, '
                    f'{payment.get_method_display().lower()}) for {_summary(booking)}. '
                    f'Balance due: {booking.balance_due}.')
    _send_now(booking)


def waitlist_opening(entry, booking):
    customer = entry.customer
    business = entry.business
    for channel in customer_channels(business, customer):
        queue(
            business=business, booking=None, event=Notification.Event.WAITLIST_OPENING, channel=channel,
            to_address=_address_for(customer, channel),
            subject=f'A slot opened up · {business.business_name}',
            body=(f'Hi {customer.full_name.split(" ")[0]}, a {entry.service.service_name} slot just opened on '
                  f'{entry.desired_date:%a %d %b}. Book it before it goes: {booking_page_link(business)}'),
            recipient_name=customer.full_name, respect_quiet_hours=True,
        )
    dispatch_due()


def retry(notification):
    if notification.status not in (Notification.Status.FAILED, Notification.Status.QUEUED):
        return notification
    notification.status = Notification.Status.QUEUED
    notification.next_attempt_at = None
    notification.attempts = 0
    notification.scheduled_for = timezone.now()
    notification.save()
    return send(notification)
