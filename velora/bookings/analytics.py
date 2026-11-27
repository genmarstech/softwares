"""Booking analytics: funnel, attendance, capacity, revenue and retention."""

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Min

from notifications.models import Notification

from .models import Booking, Customer, FunnelEvent, Payment
from .services import business_tz, get_policy, working_intervals
from business.models import BusinessStaff


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def _money(value):
    return str(Decimal(value or 0).quantize(Decimal('0.01')))


def filtered_bookings(business, date_from, date_to, staff=None, service=None, location=None):
    tz = business_tz(get_policy(business))
    start = datetime.combine(date_from, time.min, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    qs = Booking.objects.filter(business=business, start__gte=start, start__lt=end)
    if staff:
        qs = qs.filter(staff_id=staff)
    if service:
        qs = qs.filter(service_id=service)
    if location:
        qs = qs.filter(location_id=location)
    return qs.select_related('service', 'staff', 'customer', 'location').prefetch_related('payments'), start, end, tz


def build_report(business, date_from, date_to, staff=None, service=None, location=None):
    bookings_qs, start, end, tz = filtered_bookings(business, date_from, date_to, staff, service, location)
    bookings = list(bookings_qs)
    S = Booking.Status

    by_status = Counter(b.status for b in bookings)
    by_channel = Counter(b.channel for b in bookings)
    total = len(bookings)
    completed = by_status[S.COMPLETED]
    no_shows = by_status[S.NO_SHOW]
    cancelled = by_status[S.CANCELLED]
    attended_or_missed = completed + no_shows

    # ---- Funnel ---------------------------------------------------------
    funnel_qs = FunnelEvent.objects.filter(business=business, created_at__gte=start, created_at__lt=end)
    step_sessions = {
        row['step']: row['n']
        for row in funnel_qs.values('step').annotate(n=Count('session_key', distinct=True))
    }
    steps = []
    previous = None
    for step in FunnelEvent.STEP_ORDER:
        n = step_sessions.get(step, 0)
        steps.append({
            'step': step, 'label': FunnelEvent.Step(step).label, 'sessions': n,
            'drop_off_pct': _pct(previous - n, previous) if previous else 0.0,
        })
        previous = n
    views = step_sessions.get('page_view', 0)
    source_rows = funnel_qs.values('source', 'step').annotate(n=Count('session_key', distinct=True))
    sources = defaultdict(lambda: {'views': 0, 'booked': 0})
    for row in source_rows:
        if row['step'] == 'page_view':
            sources[row['source']]['views'] = row['n']
        elif row['step'] == 'booked':
            sources[row['source']]['booked'] = row['n']
    channel_conversion = [
        {'source': k, 'views': v['views'], 'booked': v['booked'], 'conversion_pct': _pct(v['booked'], v['views'])}
        for k, v in sorted(sources.items())
    ]

    # ---- Attendance & reliability --------------------------------------
    late_cancellations = sum(1 for b in bookings if b.status == S.CANCELLED and b.is_late_cancellation)
    rescheduled = [b for b in bookings if b.reschedule_count > 0]
    reminded_ids = set(Notification.objects.filter(
        booking__in=[b.id for b in bookings], event=Notification.Event.REMINDER,
        status__in=[Notification.Status.SENT, Notification.Status.DELIVERED],
    ).values_list('booking_id', flat=True))
    with_reminder = [b for b in bookings if b.id in reminded_ids and b.status in (S.COMPLETED, S.NO_SHOW)]
    without_reminder = [b for b in bookings if b.id not in reminded_ids and b.status in (S.COMPLETED, S.NO_SHOW)]

    # ---- Capacity & utilisation ----------------------------------------
    staff_members = BusinessStaff.objects.filter(business=business)
    if staff:
        staff_members = staff_members.filter(pk=staff)
    occupied = [b for b in bookings if b.status in (S.CONFIRMED, S.RESCHEDULED, S.COMPLETED, S.PENDING, S.HELD)]
    staff_rows = []
    total_available = total_booked = 0.0
    for member in staff_members:
        available_minutes = 0.0
        day = date_from
        while day <= date_to:
            for iv in working_intervals(business, member, day, tz):
                available_minutes += (iv.end - iv.start).total_seconds() / 60
            day += timedelta(days=1)
        booked_minutes = sum((b.end - b.start).total_seconds() / 60 for b in occupied if b.staff_id == member.id)
        total_available += available_minutes
        total_booked += booked_minutes
        staff_rows.append({
            'staff_id': member.id, 'staff_name': member.staff_name,
            'available_hours': round(available_minutes / 60, 1), 'booked_hours': round(booked_minutes / 60, 1),
            'idle_hours': round(max(available_minutes - booked_minutes, 0) / 60, 1),
            'occupancy_pct': _pct(booked_minutes, available_minutes),
            'bookings': sum(1 for b in bookings if b.staff_id == member.id),
        })
    hours = Counter(b.start.astimezone(tz).hour for b in occupied)
    weekdays = Counter(b.start.astimezone(tz).strftime('%A') for b in occupied)
    resources = Counter(b.resource.name for b in occupied if b.resource_id)

    # ---- Revenue ---------------------------------------------------------
    payments = [p for b in bookings for p in b.payments.all()]
    collected = Decimal('0')
    by_method = defaultdict(Decimal)
    by_kind = defaultdict(Decimal)
    for p in payments:
        sign = -1 if p.kind == Payment.Kind.REFUND else 1
        collected += sign * p.amount
        by_method[p.get_method_display()] += sign * p.amount
        by_kind[p.get_kind_display()] += p.amount

    def revenue_by(key):
        rows = defaultdict(lambda: {'bookings': 0, 'booked_value': Decimal('0'), 'collected': Decimal('0')})
        for b in bookings:
            label = key(b)
            row = rows[label]
            row['bookings'] += 1
            if b.status in (S.COMPLETED, S.CONFIRMED, S.RESCHEDULED, S.PENDING, S.HELD):
                row['booked_value'] += b.price
            row['collected'] += b.amount_paid
        return [
            {'name': k, 'bookings': v['bookings'], 'booked_value': _money(v['booked_value']),
             'collected': _money(v['collected'])}
            for k, v in sorted(rows.items(), key=lambda kv: -kv[1]['collected'])
        ]

    # ---- Customers & retention -----------------------------------------
    customer_ids = {b.customer_id for b in bookings}
    first_dates = dict(
        Customer.objects.filter(pk__in=customer_ids).annotate(first=Min('bookings__start'))
        .values_list('id', 'first')
    )
    new_customers = sum(1 for cid in customer_ids if first_dates.get(cid) and first_dates[cid] >= start)
    returning = len(customer_ids) - new_customers
    visits = defaultdict(list)
    for b in Booking.objects.filter(business=business, customer_id__in=customer_ids,
                                    status=S.COMPLETED).order_by('start'):
        visits[b.customer_id].append(b.start)
    gaps = []
    for dates in visits.values():
        gaps += [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
    repeat_customers = sum(1 for cid in customer_ids if len(visits.get(cid, [])) > 1)
    first_channels = Counter(
        Customer.objects.filter(pk__in=customer_ids).values_list('first_channel', flat=True)
    )

    # ---- Trend -------------------------------------------------------------
    per_day = Counter(b.start.astimezone(tz).date().isoformat() for b in bookings)
    trend = []
    day = date_from
    while day <= date_to:
        trend.append({'date': day.isoformat(), 'bookings': per_day.get(day.isoformat(), 0)})
        day += timedelta(days=1)

    return {
        'range': {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(), 'timezone': str(tz)},
        'bookings': {
            'total': total,
            'by_status': {k: by_status.get(k, 0) for k in S.values},
            'by_channel': dict(by_channel),
            'trend': trend,
        },
        'funnel': {
            'steps': steps,
            'conversion_pct': _pct(step_sessions.get('booked', 0), views),
            'by_source': channel_conversion,
        },
        'attendance': {
            'no_show_rate_pct': _pct(no_shows, attended_or_missed),
            'cancellation_rate_pct': _pct(cancelled, total),
            'late_cancellations': late_cancellations,
            'rescheduled_bookings': len(rescheduled),
            'reschedule_rate_pct': _pct(len(rescheduled), total),
            'reminder_effectiveness': {
                'no_show_rate_with_reminder_pct': _pct(sum(1 for b in with_reminder if b.status == S.NO_SHOW),
                                                       len(with_reminder)),
                'no_show_rate_without_reminder_pct': _pct(
                    sum(1 for b in without_reminder if b.status == S.NO_SHOW), len(without_reminder)),
                'reminded_bookings': len(with_reminder),
            },
        },
        'capacity': {
            'occupancy_pct': _pct(total_booked, total_available),
            'booked_hours': round(total_booked / 60, 1),
            'available_hours': round(total_available / 60, 1),
            'idle_hours': round(max(total_available - total_booked, 0) / 60, 1),
            'staff': staff_rows,
            'peak_hours': [{'hour': h, 'bookings': n} for h, n in sorted(hours.items())],
            'busiest_days': [{'day': d, 'bookings': n} for d, n in weekdays.most_common()],
            'resources': [{'name': n, 'bookings': c} for n, c in resources.most_common()],
        },
        'revenue': {
            'collected': _money(collected),
            'booked_value': _money(sum((b.price for b in bookings if b.status in (
                S.COMPLETED, S.CONFIRMED, S.RESCHEDULED, S.PENDING, S.HELD)), Decimal('0'))),
            'completed_value': _money(sum((b.price for b in bookings if b.status == S.COMPLETED), Decimal('0'))),
            'deposits_collected': _money(by_kind.get('Deposit', 0)),
            'cancellation_fees_charged': _money(sum((b.cancellation_fee for b in bookings), Decimal('0'))),
            'cancellation_fees_collected': _money(by_kind.get('Cancellation fee', 0)),
            'refunds': _money(by_kind.get('Refund', 0)),
            'by_service': revenue_by(lambda b: b.service.service_name),
            'by_staff': revenue_by(lambda b: b.staff.staff_name if b.staff_id else 'Unassigned'),
            'by_location': revenue_by(lambda b: b.location.name if b.location_id else 'Main location'),
            'by_payment_method': [{'method': k, 'amount': _money(v)} for k, v in sorted(by_method.items())],
        },
        'customers': {
            'unique': len(customer_ids),
            'new': new_customers,
            'returning': returning,
            'repeat_rate_pct': _pct(repeat_customers, len(customer_ids)),
            'avg_days_between_visits': round(sum(gaps) / len(gaps), 1) if gaps else None,
            'top_channels': [{'channel': k or 'unknown', 'customers': v} for k, v in first_channels.most_common()],
        },
    }


BOOKING_CSV_COLUMNS = [
    'id', 'date', 'time', 'customer', 'email', 'phone', 'service', 'staff', 'location', 'status', 'channel',
    'price', 'deposit', 'paid', 'balance_due', 'cancellation_fee', 'late_cancellation', 'rescheduled', 'created',
]


def bookings_csv(business, date_from, date_to, **filters):
    bookings_qs, _, _, tz = filtered_bookings(business, date_from, date_to, **filters)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(BOOKING_CSV_COLUMNS)
    for b in bookings_qs.order_by('start'):
        local = b.start.astimezone(tz)
        writer.writerow([
            b.id, local.date().isoformat(), local.strftime('%H:%M'), b.customer.full_name, b.customer.email,
            b.customer.phone_number, b.service.service_name, b.staff.staff_name if b.staff_id else '',
            b.location.name if b.location_id else '', b.get_status_display(), b.get_channel_display(), b.price,
            b.deposit_amount, b.amount_paid, b.balance_due, b.cancellation_fee,
            'yes' if b.is_late_cancellation else 'no', b.reschedule_count,
            b.created_at.astimezone(tz).isoformat(timespec='minutes'),
        ])
    return out.getvalue()


def summary_csv(report):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['section', 'metric', 'value'])
    r = report
    rows = [
        ('bookings', 'total', r['bookings']['total']),
        *[('bookings', f'status: {k}', v) for k, v in r['bookings']['by_status'].items()],
        ('funnel', 'conversion %', r['funnel']['conversion_pct']),
        ('attendance', 'no-show rate %', r['attendance']['no_show_rate_pct']),
        ('attendance', 'cancellation rate %', r['attendance']['cancellation_rate_pct']),
        ('attendance', 'late cancellations', r['attendance']['late_cancellations']),
        ('capacity', 'occupancy %', r['capacity']['occupancy_pct']),
        ('capacity', 'idle hours', r['capacity']['idle_hours']),
        ('revenue', 'collected', r['revenue']['collected']),
        ('revenue', 'booked value', r['revenue']['booked_value']),
        ('revenue', 'deposits collected', r['revenue']['deposits_collected']),
        ('revenue', 'cancellation fees charged', r['revenue']['cancellation_fees_charged']),
        *[('revenue by service', row['name'], row['collected']) for row in r['revenue']['by_service']],
        *[('revenue by staff', row['name'], row['collected']) for row in r['revenue']['by_staff']],
        *[('staff occupancy %', row['staff_name'], row['occupancy_pct']) for row in r['capacity']['staff']],
        ('customers', 'unique', r['customers']['unique']),
        ('customers', 'new', r['customers']['new']),
        ('customers', 'returning', r['customers']['returning']),
        ('customers', 'repeat rate %', r['customers']['repeat_rate_pct']),
    ]
    writer.writerows(rows)
    return out.getvalue()
