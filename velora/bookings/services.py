"""Availability engine and booking state machine.

All booking writes go through this module so the same rules apply whether a
customer books online, a receptionist adds a walk-in, or an API client calls in.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from business.models import BusinessStaff, Service
from .models import (
    Booking, BookingEvent, BookingPolicy, Customer, Payment, Resource, TimeOff, WaitlistEntry,
    WorkingHours, active_bookings_q,
)

DEFAULT_OPEN = time(9, 0)
DEFAULT_CLOSE = time(18, 0)


class BookingError(Exception):
    """A booking rule was broken. ``field`` names the input at fault, if any."""

    def __init__(self, message, field=None, code='invalid'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code

    def as_dict(self):
        if self.field:
            return {self.field: [self.message]}
        return {'detail': self.message}


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def ensure_business_defaults(business):
    """Create the booking policy and Mon-Sat opening hours if they are missing."""
    policy, _ = BookingPolicy.objects.get_or_create(business=business)
    if not WorkingHours.objects.filter(business=business, staff__isnull=True).exists():
        WorkingHours.objects.bulk_create([
            WorkingHours(
                business=business, weekday=day, start_time=DEFAULT_OPEN, end_time=DEFAULT_CLOSE,
                is_closed=(day == 6),
            )
            for day in range(7)
        ])
    return policy


def get_policy(business):
    try:
        return business.booking_policy
    except BookingPolicy.DoesNotExist:
        return ensure_business_defaults(business)


def business_tz(policy):
    try:
        return ZoneInfo(policy.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def deposit_for(service, policy):
    if not policy.deposit_required:
        return Decimal('0')
    if service.deposit_amount is not None:
        return money(service.deposit_amount)
    return money(service.price * policy.deposit_percentage / Decimal('100'))


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other_start, other_end):
        return self.start < other_end and other_start < self.end


def subtract(intervals, block_start, block_end):
    result = []
    for iv in intervals:
        if block_end <= iv.start or block_start >= iv.end:
            result.append(iv)
            continue
        if block_start > iv.start:
            result.append(Interval(iv.start, block_start))
        if block_end < iv.end:
            result.append(Interval(block_end, iv.end))
    return result


def eligible_staff(business, service, staff=None):
    qs = BusinessStaff.objects.filter(business=business, is_available=True).prefetch_related('skills')
    if staff is not None:
        qs = qs.filter(pk=staff.pk)
    result = []
    for member in qs:
        skill_ids = {s.id for s in member.skills.all()}
        if not skill_ids or service.id in skill_ids:
            result.append(member)
    return result


def _hours_for_day(business, staff, weekday):
    rows = list(WorkingHours.objects.filter(business=business, staff=staff, weekday=weekday))
    if not rows and staff is not None:
        rows = list(WorkingHours.objects.filter(business=business, staff__isnull=True, weekday=weekday))
    return rows


def working_intervals(business, staff, day, tz):
    """Working time for ``staff`` (or the business) on a local date, as aware datetimes."""
    intervals = []
    for row in _hours_for_day(business, staff, day.weekday()):
        if row.is_closed:
            continue
        start = datetime.combine(day, row.start_time, tzinfo=tz)
        end = datetime.combine(day, row.end_time, tzinfo=tz)
        if end <= start:
            continue
        day_intervals = [Interval(start, end)]
        if row.break_start and row.break_end and row.break_end > row.break_start:
            day_intervals = subtract(
                day_intervals,
                datetime.combine(day, row.break_start, tzinfo=tz),
                datetime.combine(day, row.break_end, tzinfo=tz),
            )
        intervals.extend(day_intervals)
    # Business-wide closures apply to everyone.
    business_hours = _hours_for_day(business, None, day.weekday())
    if staff is not None and business_hours and all(r.is_closed for r in business_hours):
        return []
    return intervals


def _busy_blocks(business, staff, window_start, window_end, exclude_booking_id=None):
    bookings = Booking.objects.filter(
        active_bookings_q(), staff=staff, block_start__lt=window_end, block_end__gt=window_start
    )
    if exclude_booking_id:
        bookings = bookings.exclude(pk=exclude_booking_id)
    blocks = [(b.block_start, b.block_end) for b in bookings]
    time_off = TimeOff.objects.filter(
        Q(staff=staff) | Q(staff__isnull=True), business=business, start__lt=window_end, end__gt=window_start
    )
    blocks += [(t.start, t.end) for t in time_off]
    return blocks


def resource_has_capacity(resource, block_start, block_end, exclude_booking_id=None):
    if resource is None:
        return True
    overlapping = Booking.objects.filter(
        active_bookings_q(), resource=resource, block_start__lt=block_end, block_end__gt=block_start
    )
    if exclude_booking_id:
        overlapping = overlapping.exclude(pk=exclude_booking_id)
    return overlapping.count() < resource.capacity


def get_available_slots(business, service, day, staff=None, enforce_window=True, exclude_booking_id=None):
    """Open start times for ``service`` on local date ``day``.

    Returns ``[{"start", "end", "staff_ids"}]`` sorted by start time. Each slot
    lists every staff member free for the whole appointment plus its buffers.
    """
    policy = get_policy(business)
    tz = business_tz(policy)
    now = timezone.now()

    if enforce_window:
        today_local = now.astimezone(tz).date()
        if day < today_local or day > today_local + timedelta(days=policy.max_advance_days):
            return []

    duration = timedelta(minutes=service.duration_minutes)
    before = timedelta(minutes=service.buffer_before_minutes)
    after = timedelta(minutes=service.buffer_after_minutes)
    step = timedelta(minutes=max(policy.slot_interval_minutes, 5))
    earliest = now + timedelta(minutes=policy.min_notice_minutes) if enforce_window else None

    day_start = datetime.combine(day, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    slots = {}
    for member in eligible_staff(business, service, staff):
        free = working_intervals(business, member, day, tz)
        if not free:
            continue
        for block_start, block_end in _busy_blocks(
            business, member, day_start - timedelta(hours=12), day_end + timedelta(hours=12), exclude_booking_id
        ):
            free = subtract(free, block_start, block_end)
        for iv in free:
            # The appointment itself must fit inside working time; buffers must
            # also fit, so prep/cleanup never spill into a break or another booking.
            cursor = iv.start + before
            while cursor + duration + after <= iv.end:
                if earliest is None or cursor >= earliest:
                    if resource_has_capacity(service.resource, cursor - before, cursor + duration + after,
                                             exclude_booking_id):
                        key = cursor.isoformat()
                        slot = slots.setdefault(key, {
                            'start': cursor, 'end': cursor + duration, 'staff_ids': [],
                        })
                        slot['staff_ids'].append(member.id)
                cursor += step
    return [slots[k] for k in sorted(slots)]




def has_open_hours(business):
    return WorkingHours.objects.filter(business=business, staff__isnull=True, is_closed=False).exists()


def confirm_opening_hours(business):
    policy = get_policy(business)
    if not policy.hours_confirmed:
        policy.hours_confirmed = True
        policy.save(update_fields=['hours_confirmed', 'updated_at'])


def hours_confirmed(business):
    if get_policy(business).hours_confirmed:
        return True
    # Businesses that already changed their hours before this flag existed count as confirmed.
    from .overview import _hours_customised
    if _hours_customised(business):
        confirm_opening_hours(business)
        return True
    return False


def service_readiness(business, service, staff_members=None, days=7):
    """Why ``service`` cannot be booked online, or '' if it can.

    Ignores existing bookings: it answers "is it set up?", not "is it free?".
    Reasons: 'hours_not_set' (the owner has not confirmed opening hours), 'no_team' (no available team member at all), 'no_skill' (nobody
    is set up to do it), 'no_hours' (nobody who can do it works in the next
    ``days`` days, or no working day is long enough for it).
    """
    if not hours_confirmed(business):
        return 'hours_not_set'
    if staff_members is None:
        staff_members = list(BusinessStaff.objects.filter(business=business, is_available=True)
                             .prefetch_related('skills'))
    if not staff_members:
        return 'no_team'
    able = [m for m in staff_members if not m.skills.all() or service.id in {s.id for s in m.skills.all()}]
    if not able:
        return 'no_skill'
    tz = business_tz(get_policy(business))
    need = timedelta(minutes=service.duration_minutes + service.buffer_before_minutes
                     + service.buffer_after_minutes)
    today = timezone.now().astimezone(tz).date()
    for offset in range(days):
        day = today + timedelta(days=offset)
        for member in able:
            if any(iv.end - iv.start >= need for iv in working_intervals(business, member, day, tz)):
                return ''
    return 'no_hours'


def booking_readiness(business):
    """Can customers book online? Lists what to fix for the owner's dashboard."""
    staff_members = list(BusinessStaff.objects.filter(business=business, is_available=True)
                         .prefetch_related('skills'))
    offered = list(Service.objects.filter(business=business, is_available=True).order_by('service_name'))
    blocked = {}
    for service in offered:
        reason = service_readiness(business, service, staff_members)
        if reason:
            blocked[service.id] = (service, reason)
    issues = []
    confirmed = hours_confirmed(business)
    if not confirmed:
        issues.append({'key': 'hours_not_set', 'link': 'schedule',
                       'text': 'Set your opening hours. Your booking link stays closed until you have confirmed '
                               'when you are open.'})
    if not offered:
        issues.append({'key': 'no_services', 'link': 'services',
                       'text': 'Add at least one service so customers have something to book.'})
    elif not staff_members:
        issues.append({'key': 'no_team', 'link': 'team',
                       'text': 'Customers cannot book online yet: add a team member (you can add yourself) '
                               'and mark them available. Open times come from your team\'s working hours.'})
    else:
        no_skill = [svc.service_name for svc, r in blocked.values() if r == 'no_skill']
        no_hours = [svc.service_name for svc, r in blocked.values() if r == 'no_hours']
        if no_skill:
            issues.append({'key': 'no_skill', 'link': 'team', 'services': no_skill,
                           'text': f'Nobody on your team is set up to do {_names(no_skill)}, so customers cannot '
                                   'book it. In Team, add it to someone\'s services (or leave their services empty '
                                   'so they can do everything).'})
        if no_hours:
            issues.append({'key': 'no_hours', 'link': 'schedule', 'services': no_hours,
                           'text': f'There are no working hours in the next 7 days long enough for '
                                   f'{_names(no_hours)}. Check your opening hours in Schedule.'})
    return {
        'ready': confirmed and bool(offered) and len(blocked) < len(offered),
        'all_ready': confirmed and bool(offered) and not blocked,
        'hours_confirmed': confirmed,
        'issues': issues,
        'unbookable_service_ids': sorted(blocked) if confirmed else [s.id for s in offered],
    }


def _names(names):
    quoted = [f'"{n}"' for n in names[:3]]
    more = len(names) - len(quoted)
    text = ', '.join(quoted)
    return f'{text} and {more} more' if more > 0 else text

# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def log_event(booking, action, actor_type=BookingEvent.Actor.SYSTEM, actor=None, from_status='',
              note='', override=False, **data):
    return BookingEvent.objects.create(
        booking=booking, action=action, from_status=from_status, to_status=booking.status,
        actor_type=actor_type, actor=actor if actor and actor.is_authenticated else None,
        note=note[:500], is_policy_override=override, data=data,
    )


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def find_or_create_customer(business, full_name, email='', phone_number='', channel=''):
    email = (email or '').strip().lower()
    phone_number = (phone_number or '').strip()
    customer = None
    if email:
        customer = Customer.objects.filter(business=business, email__iexact=email).first()
    if customer is None and phone_number:
        customer = Customer.objects.filter(business=business, phone_number=phone_number).first()
    if customer is None:
        return Customer.objects.create(
            business=business, full_name=full_name.strip(), email=email, phone_number=phone_number,
            first_channel=channel,
        )
    changed = False
    if email and not customer.email:
        customer.email, changed = email, True
    if phone_number and not customer.phone_number:
        customer.phone_number, changed = phone_number, True
    if full_name.strip() and not customer.full_name:
        customer.full_name, changed = full_name.strip(), True
    if changed:
        customer.save(update_fields=['full_name', 'email', 'phone_number', 'updated_at'])
    return customer


# ---------------------------------------------------------------------------
# Booking creation and changes
# ---------------------------------------------------------------------------

def _lock_and_pick_staff(business, service, start, staff, exclude_booking_id=None, require_hours=True):
    """Lock candidate staff rows and return one that is free for ``start``.

    ``select_for_update`` serialises concurrent bookings for the same staff
    member, so two customers racing for one slot cannot both win.
    """
    before = timedelta(minutes=service.buffer_before_minutes)
    after = timedelta(minutes=service.buffer_after_minutes)
    end = start + timedelta(minutes=service.duration_minutes)
    block_start, block_end = start - before, end + after

    candidates = eligible_staff(business, service, staff)
    if staff is not None and not candidates:
        raise BookingError('This staff member does not offer this service.', 'staff')
    if not candidates:
        raise BookingError('No staff member can perform this service yet.', 'service')

    ids = [c.id for c in candidates]
    locked = {s.id: s for s in BusinessStaff.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    if service.resource_id:
        Resource.objects.select_for_update().filter(pk=service.resource_id).first()

    policy = get_policy(business)
    tz = business_tz(policy)
    local_day = start.astimezone(tz).date()

    for member_id in ids:
        member = locked.get(member_id)
        if member is None:
            continue
        if require_hours:
            within = any(
                iv.start <= block_start and block_end <= iv.end
                for iv in working_intervals(business, member, local_day, tz)
            )
            if not within:
                continue
        busy = _busy_blocks(business, member, block_start, block_end, exclude_booking_id)
        clashes = any(bs < block_end and block_start < be for bs, be in busy)
        if clashes:
            continue
        if not resource_has_capacity(service.resource, block_start, block_end, exclude_booking_id):
            raise BookingError('The room or equipment for this service is fully booked at that time.', 'start')
        return member, end, block_start, block_end

    if staff is not None:
        raise BookingError(f'{staff.staff_name} is not available at that time.', 'start', code='conflict')
    raise BookingError('That time is no longer available. Please pick another slot.', 'start', code='conflict')


def _check_booking_window(policy, start):
    now = timezone.now()
    if start < now + timedelta(minutes=policy.min_notice_minutes):
        hours = policy.min_notice_minutes / 60
        raise BookingError(f'Bookings need at least {hours:g} hour(s) notice.', 'start')
    if start > now + timedelta(days=policy.max_advance_days + 1):
        raise BookingError(f'Bookings can be made up to {policy.max_advance_days} days ahead.', 'start')


@transaction.atomic
def create_booking(*, business, service, customer, start, staff=None, channel=Booking.Channel.ONLINE,
                   intake_notes='', internal_notes='', actor=None, actor_type=BookingEvent.Actor.CUSTOMER,
                   enforce_policy=True, location=None, status=None):
    """Create a booking after checking policy, working hours and conflicts."""
    from notifications import services as notify

    if service.business_id != business.id or not service.is_available:
        raise BookingError('This service is not available.', 'service')
    if staff is not None and staff.business_id != business.id:
        raise BookingError('Unknown staff member.', 'staff')
    if timezone.is_naive(start):
        raise BookingError('Start time must include a time zone.', 'start')

    policy = get_policy(business)
    walk_in = channel == Booking.Channel.WALK_IN
    if enforce_policy and not walk_in:
        _check_booking_window(policy, start)

    member, end, block_start, block_end = _lock_and_pick_staff(
        business, service, start, staff, require_hours=not walk_in
    )

    deposit = deposit_for(service, policy)
    if status is None:
        if walk_in:
            status = Booking.Status.CONFIRMED
        elif deposit > 0 and channel == Booking.Channel.ONLINE:
            status = Booking.Status.HELD
        elif policy.auto_confirm or actor_type == BookingEvent.Actor.OWNER:
            status = Booking.Status.CONFIRMED
        else:
            status = Booking.Status.PENDING

    booking = Booking.objects.create(
        business=business, customer=customer, service=service, staff=member, resource=service.resource,
        location=location, start=start, end=end, block_start=block_start, block_end=block_end,
        status=status, channel=channel, intake_notes=intake_notes, internal_notes=internal_notes,
        price=service.price, deposit_amount=deposit,
        hold_expires_at=(timezone.now() + timedelta(minutes=policy.hold_minutes)
                         if status == Booking.Status.HELD else None),
        confirmed_at=timezone.now() if status == Booking.Status.CONFIRMED else None,
        checked_in_at=timezone.now() if walk_in else None,
    )
    log_event(booking, 'created', actor_type=actor_type, actor=actor, override=not enforce_policy,
              channel=channel)

    transaction.on_commit(lambda: notify.booking_created(booking))
    return booking


def _assert_transition(booking, allowed_from, action):
    if not booking.is_active and booking.status in allowed_from and booking.status == Booking.Status.HELD:
        raise BookingError('This booking hold has expired.', code='expired')
    if booking.status not in allowed_from:
        raise BookingError(f'A {booking.get_status_display().lower()} booking cannot be {action}.',
                           code='invalid_status')


@transaction.atomic
def confirm_booking(booking, actor=None, actor_type=BookingEvent.Actor.OWNER):
    from notifications import services as notify
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if booking.status == Booking.Status.CONFIRMED:
        return booking
    _assert_transition(booking, [Booking.Status.PENDING, Booking.Status.HELD, Booking.Status.RESCHEDULED],
                       'confirmed')
    previous = booking.status
    booking.status = Booking.Status.CONFIRMED
    booking.confirmed_at = timezone.now()
    booking.hold_expires_at = None
    booking.save(update_fields=['status', 'confirmed_at', 'hold_expires_at', 'updated_at'])
    log_event(booking, 'confirmed', actor_type=actor_type, actor=actor, from_status=previous)
    transaction.on_commit(lambda: notify.booking_confirmed(booking))
    return booking


def hours_until(booking):
    return (booking.start - timezone.now()).total_seconds() / 3600


@transaction.atomic
def cancel_booking(booking, *, actor=None, actor_type=BookingEvent.Actor.CUSTOMER, reason='', override=False):
    from notifications import services as notify
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if booking.status == Booking.Status.CANCELLED:
        return booking
    _assert_transition(booking, list(Booking.ACTIVE_STATUSES), 'cancelled')
    policy = get_policy(booking.business)

    late = hours_until(booking) < policy.cancellation_window_hours
    if late and actor_type == BookingEvent.Actor.CUSTOMER and not override:
        raise BookingError(
            f'Online cancellation closes {policy.cancellation_window_hours} hours before the appointment. '
            'Please contact the business.', code='policy',
        )

    previous = booking.status
    booking.status = Booking.Status.CANCELLED
    booking.cancelled_at = timezone.now()
    booking.cancelled_by = actor_type
    booking.cancellation_reason = reason[:255]
    booking.is_late_cancellation = late
    if late and policy.cancellation_fee_percentage > 0 and actor_type != BookingEvent.Actor.SYSTEM:
        booking.cancellation_fee = money(booking.price * policy.cancellation_fee_percentage / Decimal('100'))
    booking.save()
    log_event(booking, 'cancelled', actor_type=actor_type, actor=actor, from_status=previous, note=reason,
              override=override and late, late=late)

    if policy.refund_deposit_on_cancellation and not late:
        deposit_paid = sum((p.amount for p in booking.payments.filter(kind=Payment.Kind.DEPOSIT)), Decimal('0'))
        refunded = sum((p.amount for p in booking.payments.filter(kind=Payment.Kind.REFUND)), Decimal('0'))
        if deposit_paid - refunded > 0:
            # Recorded as owed; the business pays it back outside Velora until a
            # payment provider is connected.
            log_event(booking, 'refund_due', actor_type=BookingEvent.Actor.SYSTEM,
                      note=f'Deposit of {deposit_paid - refunded} should be refunded.',
                      amount=str(deposit_paid - refunded))

    transaction.on_commit(lambda: notify.booking_cancelled(booking))
    transaction.on_commit(lambda: notify_waitlist(booking))
    return booking


@transaction.atomic
def reschedule_booking(booking, *, start, staff=None, actor=None, actor_type=BookingEvent.Actor.CUSTOMER,
                       override=False):
    from notifications import services as notify
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    _assert_transition(booking, list(Booking.ACTIVE_STATUSES), 'rescheduled')
    policy = get_policy(booking.business)

    if actor_type == BookingEvent.Actor.CUSTOMER and not override:
        if hours_until(booking) < policy.reschedule_window_hours:
            raise BookingError(
                f'Online rescheduling closes {policy.reschedule_window_hours} hours before the appointment. '
                'Please contact the business.', code='policy',
            )
        if booking.reschedule_count >= policy.max_reschedules:
            raise BookingError('This booking has been rescheduled the maximum number of times.', code='policy')
    if timezone.is_naive(start):
        raise BookingError('Start time must include a time zone.', 'start')
    if not override:
        _check_booking_window(policy, start)

    member, end, block_start, block_end = _lock_and_pick_staff(
        booking.business, booking.service, start, staff or booking.staff, exclude_booking_id=booking.pk
    )

    previous_status, previous_start = booking.status, booking.start
    booking.start, booking.end = start, end
    booking.block_start, booking.block_end = block_start, block_end
    booking.staff = member
    booking.reschedule_count += 1
    if booking.status != Booking.Status.HELD:
        booking.status = Booking.Status.RESCHEDULED
    booking.save()
    log_event(booking, 'rescheduled', actor_type=actor_type, actor=actor, from_status=previous_status,
              override=override, previous_start=previous_start.isoformat(), new_start=start.isoformat())

    transaction.on_commit(lambda: notify.booking_rescheduled(booking, previous_start))
    return booking


@transaction.atomic
def complete_booking(booking, actor=None):
    from notifications import services as notify
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    _assert_transition(booking, [Booking.Status.CONFIRMED, Booking.Status.RESCHEDULED, Booking.Status.PENDING],
                       'completed')
    previous = booking.status
    booking.status = Booking.Status.COMPLETED
    booking.save(update_fields=['status', 'updated_at'])
    log_event(booking, 'completed', actor_type=BookingEvent.Actor.OWNER, actor=actor, from_status=previous)
    transaction.on_commit(lambda: notify.booking_completed(booking))
    return booking


@transaction.atomic
def mark_no_show(booking, actor=None):
    from notifications import services as notify
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    _assert_transition(booking, [Booking.Status.CONFIRMED, Booking.Status.RESCHEDULED, Booking.Status.PENDING],
                       'marked as a no-show')
    if booking.start > timezone.now():
        raise BookingError('A booking can only be marked as a no-show after it starts.', code='invalid_status')
    policy = get_policy(booking.business)
    previous = booking.status
    booking.status = Booking.Status.NO_SHOW
    if policy.cancellation_fee_percentage > 0:
        booking.cancellation_fee = money(booking.price * policy.cancellation_fee_percentage / Decimal('100'))
    booking.save(update_fields=['status', 'cancellation_fee', 'updated_at'])
    log_event(booking, 'no_show', actor_type=BookingEvent.Actor.OWNER, actor=actor, from_status=previous)
    transaction.on_commit(lambda: notify.booking_no_show(booking))
    return booking


@transaction.atomic
def check_in(booking, actor=None):
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    _assert_transition(booking, list(Booking.ACTIVE_STATUSES), 'checked in')
    booking.checked_in_at = timezone.now()
    booking.save(update_fields=['checked_in_at', 'updated_at'])
    minutes_late = (booking.checked_in_at - booking.start).total_seconds() / 60
    policy = get_policy(booking.business)
    log_event(booking, 'checked_in', actor_type=BookingEvent.Actor.OWNER, actor=actor, from_status=booking.status,
              late_minutes=max(0, round(minutes_late)),
              beyond_grace=minutes_late > policy.late_arrival_grace_minutes)
    return booking


@transaction.atomic
def record_payment(booking, *, amount, kind, method=Payment.Method.CASH, reference='', actor=None):
    from notifications import services as notify
    amount = money(amount)
    if amount <= 0:
        raise BookingError('Amount must be more than zero.', 'amount')
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    payment = Payment.objects.create(
        booking=booking, amount=amount, kind=kind, method=method, reference=reference, recorded_by=actor,
    )
    log_event(booking, 'payment_recorded', actor_type=BookingEvent.Actor.OWNER, actor=actor,
              from_status=booking.status, amount=str(amount), kind=kind, method=method)
    # A held booking is confirmed once its deposit is covered.
    if booking.status == Booking.Status.HELD and booking.deposit_paid:
        booking.status = Booking.Status.CONFIRMED
        booking.confirmed_at = timezone.now()
        booking.hold_expires_at = None
        booking.save(update_fields=['status', 'confirmed_at', 'hold_expires_at', 'updated_at'])
        log_event(booking, 'confirmed', from_status=Booking.Status.HELD, note='Deposit received')
        transaction.on_commit(lambda: notify.booking_confirmed(booking))
    transaction.on_commit(lambda: notify.payment_receipt(payment))
    return payment


def expire_holds(now=None):
    """Cancel held bookings whose deposit window has passed. Returns the count."""
    from notifications import services as notify
    now = now or timezone.now()
    expired = 0
    for booking in Booking.objects.filter(status=Booking.Status.HELD, hold_expires_at__lte=now):
        with transaction.atomic():
            locked = Booking.objects.select_for_update().get(pk=booking.pk)
            if locked.status != Booking.Status.HELD:
                continue
            locked.status = Booking.Status.CANCELLED
            locked.cancelled_at = now
            locked.cancelled_by = BookingEvent.Actor.SYSTEM
            locked.cancellation_reason = 'Deposit not received in time'
            locked.save()
            log_event(locked, 'hold_expired', from_status=Booking.Status.HELD)
            transaction.on_commit(lambda b=locked: notify.hold_expired(b))
            transaction.on_commit(lambda b=locked: notify_waitlist(b))
            expired += 1
    return expired


def notify_waitlist(booking):
    """Tell waiting customers a slot opened up on the cancelled booking's date."""
    from notifications import services as notify
    policy = get_policy(booking.business)
    local_day = booking.start.astimezone(business_tz(policy)).date()
    entries = WaitlistEntry.objects.filter(
        business=booking.business, service=booking.service, desired_date=local_day,
        status=WaitlistEntry.Status.WAITING,
    ).filter(Q(staff__isnull=True) | Q(staff=booking.staff)).select_related('customer')
    for entry in entries[:5]:
        notify.waitlist_opening(entry, booking)
        entry.status = WaitlistEntry.Status.NOTIFIED
        entry.notified_at = timezone.now()
        entry.save(update_fields=['status', 'notified_at'])
