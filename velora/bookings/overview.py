"""Numbers for the owner dashboard's Overview page.

Everything here is read-only and computed from real records: bookings, services,
staff, working hours and payments. Dates are the business's local dates.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from business.models import BusinessStaff, Service
from customers.services import SETUP_SHARE_TARGET, reached_customers
from notifications.models import Notification

from .models import Booking, FunnelEvent, Payment, TimeOff, WorkingHours
from .services import DEFAULT_CLOSE, DEFAULT_OPEN, business_tz, get_policy, working_intervals

S = Booking.Status
# Bookings that count towards revenue: everything except cancelled and no-shows.
VALUE_STATUSES = (S.PENDING, S.HELD, S.CONFIRMED, S.RESCHEDULED, S.COMPLETED)
SHOWN_STATUSES = VALUE_STATUSES  # cancelled and no-shows are left off the day's timeline


def _money(value):
    return str(Decimal(value or 0).quantize(Decimal('0.01')))


def _change_pct(current, previous):
    """Percentage change, or None when there is nothing to compare with."""
    if not previous:
        return None
    return round(100 * (float(current) - float(previous)) / float(previous), 1)


def _day_bounds(day, tz):
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _counts_by_day(business, first_day, last_day, tz, statuses=VALUE_STATUSES):
    start, _ = _day_bounds(first_day, tz)
    _, end = _day_bounds(last_day, tz)
    rows = Booking.objects.filter(
        business=business, start__gte=start, start__lt=end, status__in=statuses
    ).values_list('start', flat=True)
    counts = Counter(s.astimezone(tz).date() for s in rows)
    days = (last_day - first_day).days + 1
    return [counts.get(first_day + timedelta(days=i), 0) for i in range(days)]


# ---------------------------------------------------------------------------
# Today's timeline
# ---------------------------------------------------------------------------

def _timeline(business, day, tz, policy, bookings):
    """The day's bookings in order, with the free gaps between them."""
    items = []
    for b in bookings:
        items.append({
            'type': 'booking',
            'id': b.id,
            'start': b.start.astimezone(tz).isoformat(),
            'end': b.end.astimezone(tz).isoformat(),
            'customer_name': b.customer.full_name,
            'service_name': b.service.service_name,
            'duration_minutes': b.service.duration_minutes,
            'staff_name': b.staff.staff_name if b.staff_id else None,
            'status': b.status,
            'status_label': b.get_status_display(),
        })

    # Free time: opening hours minus every booked block, from now on for today.
    min_gap = max(policy.slot_interval_minutes, 30)
    now = timezone.now()
    busy = sorted((b.start, b.end) for b in bookings if b.is_active or b.status == S.COMPLETED)
    gaps = []
    for interval in working_intervals(business, None, day, tz):
        cursor = max(interval.start, now)
        if cursor > interval.start:
            # Round up to the next slot so "Available from 10:07" reads as 10:15.
            step = policy.slot_interval_minutes or 15
            local = cursor.astimezone(tz)
            extra = (-(local.minute % step)) % step
            cursor = (local + timedelta(minutes=extra)).replace(second=0, microsecond=0)
        for start, end in busy:
            if end <= cursor or start >= interval.end:
                continue
            if start > cursor:
                gaps.append((cursor, min(start, interval.end)))
            cursor = max(cursor, end)
        if cursor < interval.end:
            gaps.append((cursor, interval.end))
    for start, end in gaps:
        minutes = int((end - start).total_seconds() // 60)
        if minutes >= min_gap:
            items.append({'type': 'available', 'start': start.astimezone(tz).isoformat(),
                          'end': end.astimezone(tz).isoformat(),
                          'minutes': minutes})
    items.sort(key=lambda item: (item['start'], item['type'] != 'available'))
    return items


# ---------------------------------------------------------------------------
# Setup checklist
# ---------------------------------------------------------------------------

def _hours_customised(business):
    if WorkingHours.objects.filter(business=business, staff__isnull=False).exists():
        return True
    if TimeOff.objects.filter(business=business).exists():
        return True
    for row in WorkingHours.objects.filter(business=business, staff__isnull=True):
        default_closed = row.weekday == 6
        if row.is_closed != default_closed:
            return True
        if not row.is_closed and (
            row.start_time != DEFAULT_OPEN or row.end_time != DEFAULT_CLOSE or row.break_start or row.break_end
        ):
            return True
    return False


def setup_steps(business):
    has_profile = bool(
        business.business_logo and business.business_description.strip()
        and business.business_email and business.whatsapp_number
    )
    has_services = Service.objects.filter(business=business, is_available=True).exists()
    has_team = BusinessStaff.objects.filter(business=business).exists()
    has_hours = _hours_customised(business)
    # Customers who joined or booked through the link (see customers.services.reached_customers).
    target = SETUP_SHARE_TARGET
    reached = reached_customers(business).count()
    shared = reached >= target
    steps = [
        {'key': 'profile', 'label': 'Business profile', 'done': has_profile, 'link': 'settings',
         'text': 'Your business information is ready.' if has_profile
         else 'Add your logo, description and contact details.'},
        {'key': 'services', 'label': 'Add services', 'done': has_services, 'link': 'services',
         'text': 'Your services have been configured.' if has_services
         else 'List what you offer, with prices and durations.'},
        {'key': 'team', 'label': 'Add your team', 'done': has_team, 'link': 'team',
         'text': 'Your staff members are registered.' if has_team else 'Register your staff members.'},
        {'key': 'hours', 'label': 'Set your availability', 'done': has_hours, 'link': 'schedule',
         'text': 'Your working hours are set.' if has_hours else 'Configure your working hours.'},
        {'key': 'share', 'label': f'Share your link with {target} customers', 'done': shared, 'link': 'share',
         'progress': {'value': min(reached, target), 'target': target, 'reached': reached},
         'text': f'{reached} customers have joined or booked through your link.' if shared
         else f'{reached} of {target} customers have joined or booked through your link so far.'},
    ]
    return {'steps': steps, 'completed': sum(s['done'] for s in steps), 'total': len(steps)}


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def build_overview(business, day):
    policy = get_policy(business)
    tz = business_tz(policy)
    now = timezone.now()
    start, end = _day_bounds(day, tz)

    day_bookings = list(
        Booking.objects.filter(business=business, start__gte=start, start__lt=end, status__in=SHOWN_STATUSES)
        .select_related('customer', 'service', 'staff').prefetch_related('payments').order_by('start')
    )

    # Today's bookings, with the six days before for the sparkline and yesterday for the change.
    last_week = _counts_by_day(business, day - timedelta(days=7), day, tz)
    today_count = last_week[-1]
    yesterday = last_week[-2]

    # Upcoming: active bookings from now (or the chosen day) through the next 7 days.
    upcoming_from = max(now, start)
    upcoming_to = start + timedelta(days=8)
    upcoming = list(
        Booking.objects.filter(business=business, start__gte=upcoming_from, start__lt=upcoming_to)
        .filter(status__in=Booking.ACTIVE_STATUSES).values_list('start', flat=True)
    )
    per_day = Counter(s.astimezone(tz).date() for s in upcoming)
    upcoming_spark = [per_day.get(day + timedelta(days=i), 0) for i in range(8)]

    services = list(Service.objects.filter(business=business).values_list('is_available', flat=True))
    staff = list(BusinessStaff.objects.filter(business=business).values_list('is_available', flat=True))
    # Sparklines for services and staff: how many were in use each day over the last two weeks.
    spark_start, _ = _day_bounds(day - timedelta(days=13), tz)
    used = defaultdict(lambda: (set(), set()))
    for when, service_id, staff_id in Booking.objects.filter(
        business=business, start__gte=spark_start, start__lt=end, status__in=VALUE_STATUSES
    ).values_list('start', 'service_id', 'staff_id'):
        services_used, staff_used = used[when.astimezone(tz).date()]
        services_used.add(service_id)
        if staff_id:
            staff_used.add(staff_id)
    span = [day - timedelta(days=13 - i) for i in range(14)]
    services_spark = [len(used[d][0]) if d in used else 0 for d in span]
    staff_spark = [len(used[d][1]) if d in used else 0 for d in span]

    # Top services over the last 30 days, each with a weekly trend.
    month_start, _ = _day_bounds(day - timedelta(days=27), tz)
    recent = Booking.objects.filter(
        business=business, start__gte=month_start, start__lt=end, status__in=VALUE_STATUSES
    ).values_list('service_id', 'service__service_name', 'start')
    by_service = defaultdict(lambda: {'name': '', 'bookings': 0, 'weeks': [0, 0, 0, 0]})
    for service_id, name, when in recent:
        row = by_service[service_id]
        row['name'] = name
        row['bookings'] += 1
        week = min(3, max(0, (when.astimezone(tz).date() - (day - timedelta(days=27))).days // 7))
        row['weeks'][week] += 1
    top_services = [
        {'service_id': sid, 'name': row['name'], 'bookings': row['bookings'], 'spark': row['weeks']}
        for sid, row in sorted(by_service.items(), key=lambda kv: -kv[1]['bookings'])[:3]
    ]

    failed_messages = Notification.objects.filter(
        business=business, status=Notification.Status.FAILED, created_at__gte=now - timedelta(days=30)
    ).count()

    return {
        'date': day.isoformat(),
        'timezone': str(tz),
        'stats': {
            'today': {'value': today_count, 'previous': yesterday,
                      'change_pct': _change_pct(today_count, yesterday), 'spark': last_week[1:]},
            'upcoming': {'value': len(upcoming), 'spark': upcoming_spark},
            # spark: services booked per day / staff with bookings per day, last 14 days
            'services': {'value': sum(services), 'total': len(services), 'spark': services_spark},
            'staff': {'value': len(staff), 'available': sum(staff), 'spark': staff_spark},
        },
        'schedule': _timeline(business, day, tz, policy, day_bookings),
        'setup': setup_steps(business),
        'top_services': top_services,
        'attention': Booking.objects.filter(
            business=business, start__gte=now, status__in=(S.PENDING, S.HELD)
        ).count(),
        'failed_messages': failed_messages,
    }


# ---------------------------------------------------------------------------
# Business performance
# ---------------------------------------------------------------------------

def _period(business, first_day, last_day, tz):
    start, _ = _day_bounds(first_day, tz)
    _, end = _day_bounds(last_day, tz)
    rows = list(
        Booking.objects.filter(business=business, start__gte=start, start__lt=end, status__in=VALUE_STATUSES)
        .values_list('start', 'price', 'customer_id')
    )
    customers = {cid for _, _, cid in rows}
    returning = set(
        Booking.objects.filter(business=business, customer_id__in=customers, start__lt=start)
        .exclude(status=S.CANCELLED).values_list('customer_id', flat=True)
    )
    return {
        'rows': rows,
        'revenue': sum((price for _, price, _ in rows), Decimal('0')),
        'bookings': len(rows),
        'customers': len(customers),
        'returning_pct': round(100 * len(returning) / len(customers), 1) if customers else None,
    }


def build_performance(business, date_from, date_to):
    tz = business_tz(get_policy(business))
    days = (date_to - date_from).days + 1
    current = _period(business, date_from, date_to, tz)
    previous = _period(business, date_from - timedelta(days=days), date_from - timedelta(days=1), tz)

    # Daily points up to a month; weekly beyond that so the chart stays readable.
    bucket_days = 1 if days <= 31 else 7
    buckets = []
    cursor = date_from
    while cursor <= date_to:
        last = min(cursor + timedelta(days=bucket_days - 1), date_to)
        buckets.append({'date': cursor.isoformat(), 'end': last.isoformat(),
                        'revenue': Decimal('0'), 'bookings': 0})
        cursor = last + timedelta(days=1)
    for when, price, _ in current['rows']:
        index = min((when.astimezone(tz).date() - date_from).days // bucket_days, len(buckets) - 1)
        buckets[index]['revenue'] += price
        buckets[index]['bookings'] += 1
    for b in buckets:
        b['revenue'] = _money(b['revenue'])

    prev_ret = previous['returning_pct']
    cur_ret = current['returning_pct']
    return {
        'range': {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(), 'timezone': str(tz)},
        'bucket': 'day' if bucket_days == 1 else 'week',
        'revenue': {'value': _money(current['revenue']), 'previous': _money(previous['revenue']),
                    'change_pct': _change_pct(current['revenue'], previous['revenue'])},
        'bookings': {'value': current['bookings'], 'previous': previous['bookings'],
                     'change_pct': _change_pct(current['bookings'], previous['bookings'])},
        'returning_clients': {
            'value_pct': cur_ret, 'previous_pct': prev_ret,
            'change_pts': round(cur_ret - prev_ret, 1) if cur_ret is not None and prev_ret is not None else None,
        },
        'series': buckets,
    }


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def build_payments(business, date_from, date_to):
    tz = business_tz(get_policy(business))
    start, _ = _day_bounds(date_from, tz)
    _, end = _day_bounds(date_to, tz)
    payments = list(
        Payment.objects.filter(booking__business=business, created_at__gte=start, created_at__lt=end)
        .select_related('booking__customer', 'booking__service').order_by('-created_at')
    )
    totals = defaultdict(Decimal)
    for p in payments:
        totals[p.kind] += p.amount
    collected = sum((p.amount for p in payments if p.kind != Payment.Kind.REFUND), Decimal('0'))
    refunds = totals[Payment.Kind.REFUND]

    # Money still owed on bookings that went ahead or are coming up.
    owed = Booking.objects.filter(business=business).filter(
        Q(status=S.COMPLETED) | Q(status__in=Booking.ACTIVE_STATUSES)
    ).prefetch_related('payments')
    outstanding = sum((b.balance_due for b in owed), Decimal('0'))

    return {
        'range': {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(), 'timezone': str(tz)},
        'summary': {
            'collected': _money(collected - refunds),
            'deposits': _money(totals[Payment.Kind.DEPOSIT]),
            'refunds': _money(refunds),
            'outstanding': _money(outstanding),
            'count': len(payments),
        },
        'payments': [
            {
                'id': p.id, 'booking': p.booking_id, 'created_at': p.created_at.isoformat(),
                'customer_name': p.booking.customer.full_name, 'service_name': p.booking.service.service_name,
                'kind': p.kind, 'kind_label': p.get_kind_display(), 'method': p.method,
                'method_label': p.get_method_display(), 'amount': _money(p.amount), 'reference': p.reference,
            }
            for p in payments[:200]
        ],
    }
