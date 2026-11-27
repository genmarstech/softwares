import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone

from business.models import Business, BusinessStaff, Service


class BookingPolicy(models.Model):
    """Scheduling rules and policies for one business."""

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name='booking_policy')

    timezone = models.CharField(max_length=64, default='Africa/Nairobi')
    slot_interval_minutes = models.PositiveIntegerField(default=15)
    min_notice_minutes = models.PositiveIntegerField(
        default=60, help_text='How long before an appointment it can still be booked online'
    )
    max_advance_days = models.PositiveIntegerField(
        default=60, help_text='How far ahead customers can book'
    )
    auto_confirm = models.BooleanField(
        default=True, help_text='Confirm online bookings straight away instead of leaving them pending'
    )

    # Cancellation, rescheduling and late arrivals
    cancellation_window_hours = models.PositiveIntegerField(
        default=24, help_text='Customers can cancel online up to this many hours before'
    )
    reschedule_window_hours = models.PositiveIntegerField(default=24)
    max_reschedules = models.PositiveIntegerField(default=2)
    late_arrival_grace_minutes = models.PositiveIntegerField(default=15)
    cancellation_fee_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        help_text='Charged on late cancellations and no-shows, as a percentage of the price',
    )

    # Deposits and refunds
    deposit_required = models.BooleanField(default=False)
    deposit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    hold_minutes = models.PositiveIntegerField(
        default=30, help_text='How long an unpaid booking holds its slot while the deposit is pending'
    )
    refund_deposit_on_cancellation = models.BooleanField(
        default=True, help_text='Refund the deposit when a customer cancels within the window'
    )
    policy_notes = models.TextField(blank=True, default='', help_text='Shown to customers when they book')

    # Notifications
    notification_channels = models.JSONField(default=list, blank=True)
    reminder_hours = models.JSONField(default=list, blank=True)
    follow_up_hours = models.PositiveIntegerField(default=2, help_text='Hours after a visit to ask for feedback')
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    notify_staff = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.notification_channels:
            self.notification_channels = ['email', 'sms']
        if not self.reminder_hours:
            self.reminder_hours = [24, 2]
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Booking policy for {self.business}'


class Location(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='locations')
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True, default='')
    is_virtual = models.BooleanField(default=False)
    meeting_link = models.URLField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Resource(models.Model):
    class Kind(models.TextChoices):
        ROOM = 'room', 'Room'
        EQUIPMENT = 'equipment', 'Equipment'

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='resources')
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='resources')
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ROOM)
    capacity = models.PositiveIntegerField(default=1, help_text='How many appointments can use it at once')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.name} ({self.get_kind_display()})'


class WorkingHours(models.Model):
    """Opening hours for one weekday. ``staff`` empty means the business default."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, 'Monday'
        TUESDAY = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY = 3, 'Thursday'
        FRIDAY = 4, 'Friday'
        SATURDAY = 5, 'Saturday'
        SUNDAY = 6, 'Sunday'

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='working_hours')
    staff = models.ForeignKey(
        BusinessStaff, on_delete=models.CASCADE, null=True, blank=True, related_name='working_hours'
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_start = models.TimeField(null=True, blank=True)
    break_end = models.TimeField(null=True, blank=True)
    is_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ['weekday', 'start_time']
        constraints = [
            UniqueConstraint(fields=['business', 'staff', 'weekday'], name='unique_hours_per_staff_day'),
            UniqueConstraint(
                fields=['business', 'weekday'], condition=Q(staff__isnull=True),
                name='unique_business_hours_per_day',
            ),
        ]

    def __str__(self):
        who = self.staff.staff_name if self.staff_id else 'Business'
        return f'{who} {self.get_weekday_display()} {self.start_time}-{self.end_time}'


class TimeOff(models.Model):
    """Blocked time: holidays, leave, or a closed business (``staff`` empty)."""

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='time_off')
    staff = models.ForeignKey(
        BusinessStaff, on_delete=models.CASCADE, null=True, blank=True, related_name='time_off'
    )
    start = models.DateTimeField()
    end = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start']


class Customer(models.Model):
    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        PUSH = 'push', 'Push'

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='customers')
    full_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default='')
    phone_number = models.CharField(max_length=20, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    preferred_channel = models.CharField(max_length=20, choices=Channel.choices, blank=True, default='')
    opt_out_email = models.BooleanField(default=False)
    opt_out_sms = models.BooleanField(default=False)
    opt_out_whatsapp = models.BooleanField(default=False)
    opt_out_push = models.BooleanField(default=False)
    first_channel = models.CharField(max_length=20, blank=True, default='', help_text='How they first booked')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['full_name']

    def opted_out(self, channel):
        return bool(getattr(self, f'opt_out_{channel}', False))

    def __str__(self):
        return self.full_name


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        HELD = 'held', 'Held'
        CONFIRMED = 'confirmed', 'Confirmed'
        RESCHEDULED = 'rescheduled', 'Rescheduled'
        CANCELLED = 'cancelled', 'Cancelled'
        COMPLETED = 'completed', 'Completed'
        NO_SHOW = 'no_show', 'No-show'

    class Channel(models.TextChoices):
        ONLINE = 'online', 'Online booking page'
        WALK_IN = 'walk_in', 'Walk-in'
        PHONE = 'phone', 'Phone'
        STAFF = 'staff', 'Added by staff'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        API = 'api', 'API'

    ACTIVE_STATUSES = (Status.PENDING, Status.HELD, Status.CONFIRMED, Status.RESCHEDULED)
    FINAL_STATUSES = (Status.CANCELLED, Status.COMPLETED, Status.NO_SHOW)

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='bookings')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='bookings')
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='bookings')
    staff = models.ForeignKey(
        BusinessStaff, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings'
    )
    resource = models.ForeignKey(Resource, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')

    start = models.DateTimeField()
    end = models.DateTimeField()
    # The time actually blocked on the calendar, including prep and cleanup buffers.
    block_start = models.DateTimeField()
    block_end = models.DateTimeField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.ONLINE)
    intake_notes = models.TextField(blank=True, default='')
    internal_notes = models.TextField(blank=True, default='')

    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    cancellation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(max_length=20, blank=True, default='')
    cancellation_reason = models.CharField(max_length=255, blank=True, default='')
    is_late_cancellation = models.BooleanField(default=False)
    reschedule_count = models.PositiveIntegerField(default=0)

    manage_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start']
        indexes = [
            models.Index(fields=['business', 'start']),
            models.Index(fields=['staff', 'block_start', 'block_end']),
            models.Index(fields=['business', 'status']),
        ]

    @property
    def is_active(self):
        if self.status == self.Status.HELD and self.hold_expires_at and self.hold_expires_at <= timezone.now():
            return False
        return self.status in self.ACTIVE_STATUSES

    @property
    def amount_paid(self):
        total = Decimal('0')
        for payment in self.payments.all():
            if payment.kind == Payment.Kind.REFUND:
                total -= payment.amount
            else:
                total += payment.amount
        return total

    @property
    def deposit_paid(self):
        paid = sum(
            (p.amount for p in self.payments.all() if p.kind == Payment.Kind.DEPOSIT), Decimal('0')
        )
        return self.deposit_amount <= 0 or paid >= self.deposit_amount

    @property
    def balance_due(self):
        due = self.price + self.cancellation_fee - self.amount_paid
        return max(due, Decimal('0'))

    def __str__(self):
        return f'{self.customer} · {self.service.service_name} · {self.start:%Y-%m-%d %H:%M}'


def active_bookings_q(now=None):
    """Bookings that still occupy their slot (unexpired holds included)."""
    now = now or timezone.now()
    return (
        Q(status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED, Booking.Status.RESCHEDULED])
        | Q(status=Booking.Status.HELD, hold_expires_at__gt=now)
        | Q(status=Booking.Status.HELD, hold_expires_at__isnull=True)
    )


class BookingEvent(models.Model):
    """Audit trail of everything that happens to a booking."""

    class Actor(models.TextChoices):
        OWNER = 'owner', 'Business owner'
        CUSTOMER = 'customer', 'Customer'
        SYSTEM = 'system', 'System'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True, default='')
    to_status = models.CharField(max_length=20, blank=True, default='')
    actor_type = models.CharField(max_length=20, choices=Actor.choices, default=Actor.SYSTEM)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default='')
    is_policy_override = models.BooleanField(default=False)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']


class Payment(models.Model):
    class Kind(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        BALANCE = 'balance', 'Balance'
        CANCELLATION_FEE = 'cancellation_fee', 'Cancellation fee'
        REFUND = 'refund', 'Refund'

    class Method(models.TextChoices):
        CASH = 'cash', 'Cash'
        CARD = 'card', 'Card'
        MOBILE_MONEY = 'mobile_money', 'Mobile money'
        BANK_TRANSFER = 'bank_transfer', 'Bank transfer'
        OTHER = 'other', 'Other'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='payments')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True, default='')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class WaitlistEntry(models.Model):
    class Status(models.TextChoices):
        WAITING = 'waiting', 'Waiting'
        NOTIFIED = 'notified', 'Notified'
        BOOKED = 'booked', 'Booked'
        REMOVED = 'removed', 'Removed'

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='waitlist')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='waitlist_entries')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='waitlist_entries')
    staff = models.ForeignKey(BusinessStaff, on_delete=models.SET_NULL, null=True, blank=True)
    desired_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['desired_date', 'created_at']


class FunnelEvent(models.Model):
    """Anonymous steps of the public booking flow, for conversion analytics."""

    class Step(models.TextChoices):
        PAGE_VIEW = 'page_view', 'Viewed booking page'
        SERVICE_SELECTED = 'service_selected', 'Chose a service'
        SLOT_SELECTED = 'slot_selected', 'Chose a time'
        DETAILS_SUBMITTED = 'details_submitted', 'Submitted details'
        BOOKED = 'booked', 'Booking created'

    STEP_ORDER = ['page_view', 'service_selected', 'slot_selected', 'details_submitted', 'booked']

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='funnel_events')
    session_key = models.CharField(max_length=64)
    step = models.CharField(max_length=30, choices=Step.choices)
    source = models.CharField(max_length=40, blank=True, default='direct')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['business', 'created_at'])]
