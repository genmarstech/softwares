from rest_framework import serializers

from business.models import BusinessStaff, Service
from business.serializers import OwnedBusinessField
from notifications.models import Notification

from .models import (
    Booking, BookingEvent, BookingPolicy, Customer, Location, Payment, Resource, TimeOff, WaitlistEntry,
    WorkingHours,
)


class SameBusinessMixin:
    """Reject related objects (staff, service, customer...) from another business."""

    same_business_fields = ()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        business = attrs.get('business', getattr(self.instance, 'business', None))
        for field in self.same_business_fields:
            obj = attrs.get(field)
            if obj is not None and business is not None and obj.business_id != business.id:
                raise serializers.ValidationError({field: ['This belongs to another business.']})
        return attrs


class BookingPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingPolicy
        exclude = ['id']
        read_only_fields = ['business', 'updated_at']

    def validate_timezone(self, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise serializers.ValidationError('Unknown time zone.')
        return value

    def validate_notification_channels(self, value):
        allowed = {'email', 'sms', 'whatsapp', 'push'}
        if not isinstance(value, list) or any(v not in allowed for v in value):
            raise serializers.ValidationError('Use any of: email, sms, whatsapp, push.')
        return value

    def validate_reminder_hours(self, value):
        if not isinstance(value, list) or any(not isinstance(v, (int, float)) or v <= 0 for v in value):
            raise serializers.ValidationError('Give a list of positive hour values, such as [24, 2].')
        return value

    def validate(self, attrs):
        pct = attrs.get('deposit_percentage')
        if pct is not None and not 0 <= pct <= 100:
            raise serializers.ValidationError({'deposit_percentage': ['Must be between 0 and 100.']})
        fee = attrs.get('cancellation_fee_percentage')
        if fee is not None and not 0 <= fee <= 100:
            raise serializers.ValidationError({'cancellation_fee_percentage': ['Must be between 0 and 100.']})
        return attrs


class LocationSerializer(serializers.ModelSerializer):
    business = OwnedBusinessField()

    class Meta:
        model = Location
        fields = '__all__'


class ResourceSerializer(SameBusinessMixin, serializers.ModelSerializer):
    business = OwnedBusinessField()
    same_business_fields = ('location',)

    class Meta:
        model = Resource
        fields = '__all__'


class WorkingHoursSerializer(SameBusinessMixin, serializers.ModelSerializer):
    business = OwnedBusinessField()
    weekday_label = serializers.CharField(source='get_weekday_display', read_only=True)
    same_business_fields = ('staff',)

    class Meta:
        model = WorkingHours
        fields = ['id', 'business', 'staff', 'weekday', 'weekday_label', 'start_time', 'end_time',
                  'break_start', 'break_end', 'is_closed']
        validators = []  # uniqueness handled below with a friendlier message

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        closed = attrs.get('is_closed', getattr(self.instance, 'is_closed', False))
        if not closed and start and end and end <= start:
            raise serializers.ValidationError({'end_time': ['Closing time must be after opening time.']})
        b_start = attrs.get('break_start', getattr(self.instance, 'break_start', None))
        b_end = attrs.get('break_end', getattr(self.instance, 'break_end', None))
        if bool(b_start) != bool(b_end):
            raise serializers.ValidationError({'break_end': ['Give both a break start and end, or neither.']})
        if b_start and b_end and (b_end <= b_start or b_start < start or b_end > end):
            raise serializers.ValidationError({'break_start': ['The break must sit inside working hours.']})
        business = attrs.get('business', getattr(self.instance, 'business', None))
        staff = attrs.get('staff', getattr(self.instance, 'staff', None))
        weekday = attrs.get('weekday', getattr(self.instance, 'weekday', None))
        clash = WorkingHours.objects.filter(business=business, staff=staff, weekday=weekday)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({'weekday': ['Hours for this day already exist. Edit them instead.']})
        return attrs


class TimeOffSerializer(SameBusinessMixin, serializers.ModelSerializer):
    business = OwnedBusinessField()
    staff_name = serializers.CharField(source='staff.staff_name', read_only=True, default=None)
    same_business_fields = ('staff',)

    class Meta:
        model = TimeOff
        fields = ['id', 'business', 'staff', 'staff_name', 'start', 'end', 'reason', 'created_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('start', getattr(self.instance, 'start', None))
        end = attrs.get('end', getattr(self.instance, 'end', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end': ['End must be after start.']})
        return attrs


class CustomerSerializer(serializers.ModelSerializer):
    business = OwnedBusinessField()
    total_bookings = serializers.IntegerField(read_only=True)
    last_visit = serializers.DateTimeField(read_only=True)
    no_shows = serializers.IntegerField(read_only=True)

    class Meta:
        model = Customer
        fields = ['id', 'business', 'full_name', 'email', 'phone_number', 'notes', 'preferred_channel',
                  'opt_out_email', 'opt_out_sms', 'opt_out_whatsapp', 'opt_out_push', 'first_channel',
                  'total_bookings', 'last_visit', 'no_shows', 'created_at', 'updated_at']
        read_only_fields = ['first_channel', 'created_at', 'updated_at']

    def validate(self, attrs):
        email = attrs.get('email', getattr(self.instance, 'email', ''))
        phone = attrs.get('phone_number', getattr(self.instance, 'phone_number', ''))
        if not email and not phone:
            raise serializers.ValidationError({'email': ['Give an email address or a phone number.']})
        business = attrs.get('business', getattr(self.instance, 'business', None))
        if email:
            clash = Customer.objects.filter(business=business, email__iexact=email)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({'email': ['A customer with this email already exists.']})
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    method_label = serializers.CharField(source='get_method_display', read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'kind', 'kind_label', 'method', 'method_label', 'amount', 'reference', 'created_at']


class BookingEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = BookingEvent
        fields = ['id', 'action', 'from_status', 'to_status', 'actor_type', 'actor_name', 'note',
                  'is_policy_override', 'data', 'created_at']

    def get_actor_name(self, obj):
        if obj.actor_id:
            return obj.actor.full_name
        return obj.get_actor_type_display()


class BookingSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    channel_label = serializers.CharField(source='get_channel_display', read_only=True)
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)
    customer_email = serializers.CharField(source='customer.email', read_only=True)
    customer_phone = serializers.CharField(source='customer.phone_number', read_only=True)
    service_name = serializers.CharField(source='service.service_name', read_only=True)
    duration_minutes = serializers.IntegerField(source='service.duration_minutes', read_only=True)
    staff_name = serializers.CharField(source='staff.staff_name', read_only=True, default=None)
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    deposit_paid = serializers.BooleanField(read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = ['id', 'business', 'customer', 'customer_name', 'customer_email', 'customer_phone',
                  'service', 'service_name', 'duration_minutes', 'staff', 'staff_name', 'resource', 'location',
                  'start', 'end', 'status', 'status_label', 'channel', 'channel_label', 'intake_notes',
                  'internal_notes', 'price', 'deposit_amount', 'deposit_paid', 'amount_paid', 'balance_due',
                  'cancellation_fee', 'hold_expires_at', 'confirmed_at', 'checked_in_at', 'cancelled_at',
                  'cancelled_by', 'cancellation_reason', 'is_late_cancellation', 'reschedule_count', 'payments',
                  'manage_token', 'created_at', 'updated_at']
        read_only_fields = fields


class BookingCreateSerializer(serializers.Serializer):
    """Input for bookings made from the dashboard (phone bookings, walk-ins)."""

    business = OwnedBusinessField()
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.all())
    staff = serializers.PrimaryKeyRelatedField(queryset=BusinessStaff.objects.all(), required=False,
                                               allow_null=True)
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all(), required=False,
                                                  allow_null=True)
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.all(), required=False,
                                                  allow_null=True)
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    start = serializers.DateTimeField(required=False)
    channel = serializers.ChoiceField(
        choices=[Booking.Channel.STAFF, Booking.Channel.PHONE, Booking.Channel.WALK_IN, Booking.Channel.WHATSAPP],
        default=Booking.Channel.STAFF,
    )
    intake_notes = serializers.CharField(required=False, allow_blank=True)
    internal_notes = serializers.CharField(required=False, allow_blank=True)
    override_policy = serializers.BooleanField(default=False)

    def validate(self, attrs):
        business = attrs['business']
        for field in ('service', 'staff', 'location', 'customer'):
            obj = attrs.get(field)
            if obj is not None and obj.business_id != business.id:
                raise serializers.ValidationError({field: ['This belongs to another business.']})
        if not attrs.get('customer') and not attrs.get('customer_name'):
            raise serializers.ValidationError({'customer_name': ['Choose a customer or enter a name.']})
        if attrs.get('channel') != Booking.Channel.WALK_IN and not attrs.get('start'):
            raise serializers.ValidationError({'start': ['Choose a start time.']})
        return attrs


class RescheduleSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    staff = serializers.PrimaryKeyRelatedField(queryset=BusinessStaff.objects.all(), required=False,
                                               allow_null=True)
    override_policy = serializers.BooleanField(default=False)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    override_policy = serializers.BooleanField(default=False)


class RecordPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    kind = serializers.ChoiceField(choices=Payment.Kind.choices)
    method = serializers.ChoiceField(choices=Payment.Method.choices, default=Payment.Method.CASH)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=120)


class WaitlistEntrySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)
    service_name = serializers.CharField(source='service.service_name', read_only=True)

    class Meta:
        model = WaitlistEntry
        fields = ['id', 'business', 'customer', 'customer_name', 'service', 'service_name', 'staff',
                  'desired_date', 'status', 'notified_at', 'created_at']
        read_only_fields = ['business', 'customer', 'service', 'staff', 'desired_date', 'notified_at',
                            'created_at']


class NotificationSerializer(serializers.ModelSerializer):
    event_label = serializers.CharField(source='get_event_display', read_only=True)

    class Meta:
        model = Notification
        fields = ['id', 'booking', 'event', 'event_label', 'channel', 'recipient_type', 'recipient_name',
                  'to_address', 'subject', 'body', 'status', 'scheduled_for', 'attempts', 'next_attempt_at',
                  'provider', 'error', 'sent_at', 'delivered_at', 'created_at']
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Public (customer-facing) serializers. These expose only what a booking page
# needs: no staff emails, ID numbers, internal notes or other customers.
# ---------------------------------------------------------------------------

class PublicServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'service_name', 'service_description', 'price', 'duration_minutes', 'deposit_amount']


class PublicStaffSerializer(serializers.ModelSerializer):
    service_ids = serializers.SerializerMethodField()

    class Meta:
        model = BusinessStaff
        fields = ['id', 'staff_name', 'service_ids']

    def get_service_ids(self, obj):
        return [s.id for s in obj.skills.all()]


class PublicBookingInputSerializer(serializers.Serializer):
    service = serializers.IntegerField()
    staff = serializers.IntegerField(required=False, allow_null=True)
    start = serializers.DateTimeField()
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    intake_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    preferred_channel = serializers.ChoiceField(choices=Customer.Channel.choices, required=False,
                                                allow_blank=True)
    marketing_opt_out = serializers.BooleanField(default=False)
    session_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    source = serializers.CharField(required=False, allow_blank=True, max_length=40)

    def validate(self, attrs):
        if not attrs.get('email') and not attrs.get('phone_number'):
            raise serializers.ValidationError({'email': ['Give an email address or a phone number.']})
        return attrs


class PublicBookingSerializer(serializers.ModelSerializer):
    """What a customer sees on their manage-booking page."""

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    service_name = serializers.CharField(source='service.service_name')
    service_id = serializers.IntegerField(source='service.id')
    duration_minutes = serializers.IntegerField(source='service.duration_minutes')
    staff_name = serializers.CharField(source='staff.staff_name', default=None)
    staff_id = serializers.IntegerField(source='staff.id', default=None)
    customer_name = serializers.CharField(source='customer.full_name')
    business_id = serializers.IntegerField(source='business.id')
    business_name = serializers.CharField(source='business.business_name')
    balance_due = serializers.DecimalField(max_digits=10, decimal_places=2)
    deposit_paid = serializers.BooleanField()

    class Meta:
        model = Booking
        fields = ['manage_token', 'status', 'status_label', 'service_id', 'service_name', 'duration_minutes',
                  'staff_id', 'staff_name', 'customer_name', 'business_id', 'business_name', 'start', 'end',
                  'price', 'deposit_amount', 'deposit_paid', 'balance_due', 'cancellation_fee',
                  'hold_expires_at', 'reschedule_count', 'intake_notes']
