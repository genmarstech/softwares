from datetime import date, datetime, timedelta

from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from business.mixins import BusinessScopedMixin, owned_businesses
from business.models import Business, BusinessStaff, Service
from customers import services as customer_accounts
from notifications import services as notify
from notifications.models import Notification

from . import services
from .models import (
    Booking, BookingEvent, Customer, FunnelEvent, Location, Resource, TimeOff, WaitlistEntry, WorkingHours,
)
from .serializers import (
    BookingCreateSerializer, BookingEventSerializer, BookingPolicySerializer, BookingSerializer,
    CancelSerializer, CustomerSerializer, LocationSerializer, NotificationSerializer, PublicBookingInputSerializer,
    PublicBookingSerializer, PublicServiceSerializer, PublicStaffSerializer, RecordPaymentSerializer,
    RescheduleSerializer, ResourceSerializer, TimeOffSerializer, WaitlistEntrySerializer, WorkingHoursSerializer,
)


def booking_error_response(exc):
    code = status.HTTP_409_CONFLICT if exc.code == 'conflict' else status.HTTP_400_BAD_REQUEST
    body = exc.as_dict()
    body.setdefault('detail', exc.message)
    body['code'] = exc.code
    return Response(body, status=code)


def parse_day(value, field='date'):
    day = parse_date(value or '')
    if day is None:
        raise ValidationError({field: ['Use the YYYY-MM-DD format.']})
    return day


def serialize_slots(slots, tz):
    return [
        {
            'start': s['start'].isoformat(),
            'end': s['end'].isoformat(),
            'label': s['start'].astimezone(tz).strftime('%H:%M'),
            'staff_ids': s['staff_ids'],
        }
        for s in slots
    ]


# ---------------------------------------------------------------------------
# Owner / staff dashboard APIs (session auth, scoped to the owner's business)
# ---------------------------------------------------------------------------

class BookingPolicyView(BusinessScopedMixin, APIView):
    def get(self, request):
        business = self.get_business()
        return Response(BookingPolicySerializer(services.get_policy(business)).data)

    def patch(self, request):
        business = self.get_business()
        serializer = BookingPolicySerializer(services.get_policy(business), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    put = patch


class LocationViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    queryset = Location.objects.order_by('name')
    serializer_class = LocationSerializer


class ResourceViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    queryset = Resource.objects.order_by('name')
    serializer_class = ResourceSerializer


class WorkingHoursViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    queryset = WorkingHours.objects.select_related('staff')
    serializer_class = WorkingHoursSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        staff = self.request.query_params.get('staff')
        if staff == 'business':
            qs = qs.filter(staff__isnull=True)
        elif staff:
            qs = qs.filter(staff_id=staff)
        return qs

    def list(self, request, *args, **kwargs):
        services.ensure_business_defaults(self.get_business())
        return super().list(request, *args, **kwargs)

    def _mark_confirmed(self, hours):
        if hours.staff_id is None:
            services.confirm_opening_hours(hours.business)

    def perform_create(self, serializer):
        self._mark_confirmed(serializer.save())

    def perform_update(self, serializer):
        self._mark_confirmed(serializer.save())

    @action(detail=False, methods=['post'])
    def confirm(self, request):
        """The owner has reviewed their opening hours (even if they kept the defaults)."""
        business = self.get_business()
        if not services.has_open_hours(business):
            return Response({'detail': 'Open at least one day so customers can book.'}, status=400)
        services.confirm_opening_hours(business)
        return Response({'hours_confirmed': True})


class TimeOffViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    queryset = TimeOff.objects.select_related('staff')
    serializer_class = TimeOffSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('upcoming') == '1':
            qs = qs.filter(end__gte=timezone.now())
        return qs


class CustomerViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            total_bookings=Count('bookings', distinct=True),
            last_visit=Max('bookings__start', filter=Q(bookings__status=Booking.Status.COMPLETED)),
            no_shows=Count('bookings', filter=Q(bookings__status=Booking.Status.NO_SHOW), distinct=True),
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(full_name__icontains=search) | Q(email__icontains=search) |
                           Q(phone_number__icontains=search))
        return qs.order_by('full_name')

    def destroy(self, request, *args, **kwargs):
        customer = self.get_object()
        if customer.bookings.exists():
            return Response({'detail': 'This customer has bookings, so they cannot be deleted.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True)
    def bookings(self, request, pk=None):
        customer = self.get_object()
        qs = customer.bookings.select_related('service', 'staff', 'customer').prefetch_related('payments')
        return Response(BookingSerializer(qs.order_by('-start'), many=True).data)


class BookingViewSet(BusinessScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Bookings of one business. Status changes go through the actions below so
    every rule and notification fires the same way."""

    queryset = Booking.objects.select_related('customer', 'service', 'staff', 'business').prefetch_related('payments')
    serializer_class = BookingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('date_from'):
            qs = qs.filter(start__date__gte=parse_day(params['date_from'], 'date_from'))
        if params.get('date_to'):
            qs = qs.filter(start__date__lte=parse_day(params['date_to'], 'date_to'))
        if params.get('status'):
            qs = qs.filter(status__in=params['status'].split(','))
        if params.get('staff'):
            qs = qs.filter(staff_id=params['staff'])
        if params.get('service'):
            qs = qs.filter(service_id=params['service'])
        if params.get('customer'):
            qs = qs.filter(customer_id=params['customer'])
        if params.get('search'):
            term = params['search'].strip()
            qs = qs.filter(Q(customer__full_name__icontains=term) | Q(customer__phone_number__icontains=term) |
                           Q(customer__email__icontains=term))
        if params.get('channel'):
            qs = qs.filter(channel__in=params['channel'].split(','))
        if params.get('booked_since'):
            since = parse_datetime(params['booked_since'])
            if since is None:
                raise ValidationError({'booked_since': ['Use an ISO date-time.']})
            qs = qs.filter(created_at__gte=since)
        # ordering=start (default, appointment time), -start, or -created_at (newest bookings first)
        ordering = params.get('ordering', 'start')
        if ordering not in ('start', '-start', '-created_at'):
            raise ValidationError({'ordering': ['Use start, -start or -created_at.']})
        return qs.order_by(ordering, 'id')

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        if request.query_params.get('limit'):
            try:
                limit = max(1, min(200, int(request.query_params['limit'])))
            except ValueError:
                raise ValidationError({'limit': ['Use a number.']})
            qs = qs[:limit]
        return Response(self.get_serializer(qs, many=True).data)

    def update(self, request, *args, **kwargs):
        # Only notes can be edited directly.
        booking = self.get_object()
        for field in ('internal_notes', 'intake_notes'):
            if field in request.data:
                setattr(booking, field, request.data.get(field) or '')
        booking.save(update_fields=['internal_notes', 'intake_notes', 'updated_at'])
        services.log_event(booking, 'notes_updated', actor_type=BookingEvent.Actor.OWNER, actor=request.user,
                           from_status=booking.status)
        return Response(BookingSerializer(booking).data)

    def create(self, request):
        serializer = BookingCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        business = data['business']
        customer = data.get('customer') or services.find_or_create_customer(
            business, data['customer_name'], data.get('customer_email', ''), data.get('customer_phone', ''),
            channel=data['channel'],
        )
        start = data.get('start') or timezone.now().replace(second=0, microsecond=0)
        try:
            booking = services.create_booking(
                business=business, service=data['service'], customer=customer, start=start,
                staff=data.get('staff'), channel=data['channel'], intake_notes=data.get('intake_notes', ''),
                internal_notes=data.get('internal_notes', ''), actor=request.user,
                actor_type=BookingEvent.Actor.OWNER, enforce_policy=not data['override_policy'],
                location=data.get('location'),
            )
        except services.BookingError as exc:
            return booking_error_response(exc)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    def _run(self, func, **kwargs):
        try:
            booking = func(self.get_object(), **kwargs)
        except services.BookingError as exc:
            return booking_error_response(exc)
        booking.refresh_from_db()
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        return self._run(services.confirm_booking, actor=request.user)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        s = CancelSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._run(services.cancel_booking, actor=request.user, actor_type=BookingEvent.Actor.OWNER,
                         reason=s.validated_data.get('reason', ''), override=s.validated_data['override_policy'])

    @action(detail=True, methods=['post'])
    def reschedule(self, request, pk=None):
        s = RescheduleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        staff = s.validated_data.get('staff')
        booking = self.get_object()
        if staff is not None and staff.business_id != booking.business_id:
            return Response({'staff': ['This belongs to another business.']}, status=400)
        return self._run(services.reschedule_booking, start=s.validated_data['start'], staff=staff,
                         actor=request.user, actor_type=BookingEvent.Actor.OWNER,
                         override=s.validated_data['override_policy'])

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self._run(services.complete_booking, actor=request.user)

    @action(detail=True, methods=['post'], url_path='no-show')
    def no_show(self, request, pk=None):
        return self._run(services.mark_no_show, actor=request.user)

    @action(detail=True, methods=['post'], url_path='check-in')
    def check_in(self, request, pk=None):
        return self._run(services.check_in, actor=request.user)

    @action(detail=True, methods=['post'])
    def payments(self, request, pk=None):
        s = RecordPaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        booking = self.get_object()
        try:
            services.record_payment(booking, actor=request.user, **s.validated_data)
        except services.BookingError as exc:
            return booking_error_response(exc)
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    @action(detail=True)
    def history(self, request, pk=None):
        booking = self.get_object()
        return Response(BookingEventSerializer(booking.events.select_related('actor'), many=True).data)

    @action(detail=True)
    def notifications(self, request, pk=None):
        booking = self.get_object()
        return Response(NotificationSerializer(booking.notifications.all(), many=True).data)


class OwnerAvailabilityView(BusinessScopedMixin, APIView):
    """Open slots, for staff booking on a customer's behalf."""

    def get(self, request):
        business = self.get_business()
        service = get_object_or_404(Service, pk=request.query_params.get('service'), business=business)
        staff_id = request.query_params.get('staff')
        staff = get_object_or_404(BusinessStaff, pk=staff_id, business=business) if staff_id else None
        day = parse_day(request.query_params.get('date'))
        enforce = request.query_params.get('override') != '1'
        slots = services.get_available_slots(business, service, day, staff=staff, enforce_window=enforce,
                                             exclude_booking_id=request.query_params.get('exclude'))
        tz = services.business_tz(services.get_policy(business))
        return Response({'date': day.isoformat(), 'timezone': str(tz), 'slots': serialize_slots(slots, tz)})


class WaitlistViewSet(BusinessScopedMixin, mixins.ListModelMixin, mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = WaitlistEntry.objects.select_related('customer', 'service')
    serializer_class = WaitlistEntrySerializer


class NotificationViewSet(BusinessScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Delivery log: everything sent, scheduled, failed or skipped."""

    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ('status', 'channel', 'event'):
            if params.get(field):
                qs = qs.filter(**{f'{field}__in': params[field].split(',')})
        return qs[:500] if self.action == 'list' else qs

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        notification = notify.retry(self.get_object())
        return Response(NotificationSerializer(notification).data)


# ---------------------------------------------------------------------------
# Public booking APIs (no login; customers manage bookings with their token)
# ---------------------------------------------------------------------------

class PublicAPIView(APIView):
    # No session auth here, so a business owner testing their own booking page
    # is treated exactly like a customer and CSRF is not needed.
    authentication_classes = []
    permission_classes = [AllowAny]


def public_business(business_ref):
    """Find a business by its booking-link slug, or by id for older links."""
    ref = str(business_ref).strip().lower()
    if ref.isdigit():
        return get_object_or_404(Business, pk=int(ref))
    return get_object_or_404(Business, slug=ref)


class PublicBusinessView(PublicAPIView):
    def get(self, request, business_id):
        business = public_business(business_id)
        policy = services.get_policy(business)
        services_qs = business.services.filter(is_available=True).order_by('service_name')
        staff_qs = BusinessStaff.objects.filter(business=business, is_available=True).prefetch_related('skills')
        logo = request.build_absolute_uri(business.business_logo.url) if business.business_logo else None
        readiness = services.booking_readiness(business)
        unbookable = set(readiness['unbookable_service_ids'])
        return Response({
            'id': business.id,
            'slug': business.slug,
            'business_name': business.business_name,
            'business_description': business.business_description,
            'business_category': business.business_category,
            'business_category_label': business.get_business_category_display(),
            'business_logo': logo,
            'whatsapp_number': business.whatsapp_number,
            'business_email': business.business_email,
            # Services nobody can currently do (no team, skills or hours) are flagged, not hidden.
            'services': [
                {**row, 'bookable': row['id'] not in unbookable}
                for row in PublicServiceSerializer(services_qs, many=True).data
            ],
            'accepting_bookings': readiness['ready'],
            'staff': PublicStaffSerializer(staff_qs, many=True).data,
            'policy': {
                'timezone': policy.timezone,
                'max_advance_days': policy.max_advance_days,
                'min_notice_minutes': policy.min_notice_minutes,
                'cancellation_window_hours': policy.cancellation_window_hours,
                'reschedule_window_hours': policy.reschedule_window_hours,
                'deposit_required': policy.deposit_required,
                'deposit_percentage': str(policy.deposit_percentage),
                'cancellation_fee_percentage': str(policy.cancellation_fee_percentage),
                'late_arrival_grace_minutes': policy.late_arrival_grace_minutes,
                'policy_notes': policy.policy_notes,
            },
        })


class PublicAvailabilityView(PublicAPIView):
    def get(self, request, business_id):
        business = public_business(business_id)
        service = get_object_or_404(Service, pk=request.query_params.get('service'), business=business,
                                    is_available=True)
        staff_id = request.query_params.get('staff')
        staff = (get_object_or_404(BusinessStaff, pk=staff_id, business=business, is_available=True)
                 if staff_id else None)
        day = parse_day(request.query_params.get('date'))
        slots = services.get_available_slots(business, service, day, staff=staff)
        tz = services.business_tz(services.get_policy(business))
        return Response({'date': day.isoformat(), 'timezone': str(tz), 'slots': serialize_slots(slots, tz)})


class PublicAvailableDaysView(PublicAPIView):
    """Which of the next N days have at least one open slot (for the date picker)."""

    def get(self, request, business_id):
        business = public_business(business_id)
        service = get_object_or_404(Service, pk=request.query_params.get('service'), business=business,
                                    is_available=True)
        staff_id = request.query_params.get('staff')
        staff = (get_object_or_404(BusinessStaff, pk=staff_id, business=business, is_available=True)
                 if staff_id else None)
        policy = services.get_policy(business)
        tz = services.business_tz(policy)
        start = parse_date(request.query_params.get('from') or '') or timezone.now().astimezone(tz).date()
        days = min(int(request.query_params.get('days') or 14), 42)
        result = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            slots = services.get_available_slots(business, service, day, staff=staff)
            result.append({'date': day.isoformat(), 'available': len(slots)})
        return Response({'timezone': str(tz), 'days': result})


class PublicBookingCreateView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_booking'

    def post(self, request, business_id):
        business = public_business(business_id)
        # A customer signed in on the booking link books as themselves; guests give their details.
        account = customer_accounts.current_account(request)
        if account is not None:
            customer_accounts.enforce_csrf(request)
        s = PublicBookingInputSerializer(data=request.data, context={'account': account})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        service = Service.objects.filter(pk=data['service'], business=business, is_available=True).first()
        if service is None:
            return Response({'service': ['This service is not available.']}, status=400)
        # The link only takes bookings once the business has a team with open times for this service.
        if services.service_readiness(business, service):
            return Response({'detail': f'{business.business_name} is not taking online bookings for this '
                                       'service yet. Please contact them directly.', 'code': 'not_ready'},
                            status=400)
        staff = None
        if data.get('staff'):
            staff = BusinessStaff.objects.filter(pk=data['staff'], business=business, is_available=True).first()
            if staff is None:
                return Response({'staff': ['This staff member is not available.']}, status=400)

        source = data.get('source') or 'direct'
        if account is not None:
            customer = customer_accounts.join_business(account, business, channel=source)
        else:
            customer = services.find_or_create_customer(
                business, data['full_name'], data.get('email', ''), data.get('phone_number', ''), channel=source,
            )
        if data.get('preferred_channel'):
            customer.preferred_channel = data['preferred_channel']
            customer.save(update_fields=['preferred_channel', 'updated_at'])
        try:
            booking = services.create_booking(
                business=business, service=service, customer=customer, start=data['start'], staff=staff,
                channel=Booking.Channel.ONLINE, intake_notes=data.get('intake_notes', ''),
            )
        except services.BookingError as exc:
            return booking_error_response(exc)
        if data.get('session_key'):
            FunnelEvent.objects.create(business=business, session_key=data['session_key'],
                                       step=FunnelEvent.Step.BOOKED, source=source)
        return Response(PublicBookingSerializer(booking).data, status=status.HTTP_201_CREATED)


class PublicManageView(PublicAPIView):
    def get_booking(self, token):
        return get_object_or_404(
            Booking.objects.select_related('service', 'staff', 'customer', 'business').prefetch_related('payments'),
            manage_token=token,
        )

    def get(self, request, token):
        booking = self.get_booking(token)
        policy = services.get_policy(booking.business)
        hours_left = services.hours_until(booking)
        data = PublicBookingSerializer(booking).data
        data['can_cancel'] = booking.is_active and hours_left >= policy.cancellation_window_hours
        data['can_reschedule'] = (booking.is_active and hours_left >= policy.reschedule_window_hours
                                  and booking.reschedule_count < policy.max_reschedules)
        data['can_confirm'] = booking.status in (Booking.Status.PENDING, Booking.Status.RESCHEDULED) and \
            booking.is_active and booking.start > timezone.now()
        data['policy'] = {
            'cancellation_window_hours': policy.cancellation_window_hours,
            'reschedule_window_hours': policy.reschedule_window_hours,
            'max_reschedules': policy.max_reschedules,
            'timezone': policy.timezone,
            'policy_notes': policy.policy_notes,
        }
        return Response(data)


class PublicCancelView(PublicManageView):
    def post(self, request, token):
        booking = self.get_booking(token)
        try:
            services.cancel_booking(booking, actor_type=BookingEvent.Actor.CUSTOMER,
                                    reason=str(request.data.get('reason', ''))[:255])
        except services.BookingError as exc:
            return booking_error_response(exc)
        return self.get(request, token)


class PublicRescheduleView(PublicManageView):
    def post(self, request, token):
        booking = self.get_booking(token)
        s = RescheduleSerializer(data={'start': request.data.get('start')})
        s.is_valid(raise_exception=True)
        staff = None
        if request.data.get('staff'):
            staff = BusinessStaff.objects.filter(pk=request.data['staff'], business=booking.business,
                                                 is_available=True).first()
        try:
            services.reschedule_booking(booking, start=s.validated_data['start'], staff=staff,
                                        actor_type=BookingEvent.Actor.CUSTOMER)
        except services.BookingError as exc:
            return booking_error_response(exc)
        return self.get(request, token)


class PublicConfirmView(PublicManageView):
    """Customer taps "confirm" in a reminder."""

    def post(self, request, token):
        booking = self.get_booking(token)
        if booking.status == Booking.Status.HELD:
            return Response({'detail': 'Please pay the deposit to confirm this booking.'}, status=400)
        try:
            services.confirm_booking(booking, actor_type=BookingEvent.Actor.CUSTOMER)
        except services.BookingError as exc:
            return booking_error_response(exc)
        return self.get(request, token)


class PublicPreferencesView(PublicManageView):
    """Customer opts in or out of channels from their booking link."""

    def post(self, request, token):
        booking = self.get_booking(token)
        customer = booking.customer
        for channel in ('email', 'sms', 'whatsapp', 'push'):
            key = f'opt_out_{channel}'
            if key in request.data:
                setattr(customer, key, bool(request.data.get(key)))
        customer.save()
        return Response({f'opt_out_{c}': getattr(customer, f'opt_out_{c}') for c in ('email', 'sms', 'whatsapp',
                                                                                   'push')})


class PublicTrackView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_track'

    def post(self, request, business_id):
        business = public_business(business_id)
        step = request.data.get('step')
        session_key = str(request.data.get('session_key') or '')[:64]
        if step not in FunnelEvent.STEP_ORDER or step == FunnelEvent.Step.BOOKED or not session_key:
            return Response({'detail': 'Unknown step.'}, status=400)
        FunnelEvent.objects.create(business=business, session_key=session_key, step=step,
                                   source=str(request.data.get('source') or 'direct')[:40])
        return Response(status=204)


class PublicWaitlistView(PublicAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_booking'

    def post(self, request, business_id):
        business = public_business(business_id)
        service = Service.objects.filter(pk=request.data.get('service'), business=business).first()
        day = parse_date(str(request.data.get('desired_date') or ''))
        name = str(request.data.get('full_name') or '').strip()
        email = str(request.data.get('email') or '').strip()
        phone = str(request.data.get('phone_number') or '').strip()
        if not service or not day or not name or not (email or phone):
            return Response({'detail': 'Choose a service and date, and give your name and email or phone.'},
                            status=400)
        customer = services.find_or_create_customer(business, name, email, phone, channel='waitlist')
        staff = BusinessStaff.objects.filter(pk=request.data.get('staff') or 0, business=business).first()
        entry, _ = WaitlistEntry.objects.get_or_create(
            business=business, customer=customer, service=service, desired_date=day, staff=staff,
            status=WaitlistEntry.Status.WAITING,
        )
        return Response({'id': entry.id, 'desired_date': day.isoformat(), 'status': entry.status}, status=201)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class AnalyticsMixin(BusinessScopedMixin):
    def report_args(self, request):
        business = self.get_business()
        tz = services.business_tz(services.get_policy(business))
        today = timezone.now().astimezone(tz).date()
        date_to = parse_date(request.query_params.get('date_to') or '') or today
        date_from = parse_date(request.query_params.get('date_from') or '') or date_to - timedelta(days=29)
        if date_from > date_to:
            raise ValidationError({'date_from': ['Must be on or before date_to.']})
        if (date_to - date_from).days > 366:
            raise ValidationError({'date_from': ['Pick a range of at most one year.']})
        filters = {k: request.query_params.get(k) or None for k in ('staff', 'service', 'location')}
        return business, date_from, date_to, filters


class AnalyticsView(AnalyticsMixin, APIView):
    def get(self, request):
        from .analytics import build_report
        business, date_from, date_to, filters = self.report_args(request)
        return Response(build_report(business, date_from, date_to, **filters))


class AnalyticsExportView(AnalyticsMixin, APIView):
    def get(self, request):
        from django.http import HttpResponse
        from .analytics import bookings_csv, build_report, summary_csv
        business, date_from, date_to, filters = self.report_args(request)
        kind = request.query_params.get('kind', 'bookings')
        if kind == 'summary':
            body = summary_csv(build_report(business, date_from, date_to, **filters))
        else:
            body = bookings_csv(business, date_from, date_to, **filters)
        response = HttpResponse(body, content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="velora-{kind}-{date_from.isoformat()}-to-{date_to.isoformat()}.csv"'
        )
        return response


# ---------------------------------------------------------------------------
# Overview page: stats, today's timeline, setup checklist, performance, payments
# ---------------------------------------------------------------------------

class OverviewView(BusinessScopedMixin, APIView):
    """GET ?business=<id>&date=YYYY-MM-DD (defaults to today in the business's time zone)."""

    def get(self, request):
        from .overview import build_overview
        business = self.get_business()
        tz = services.business_tz(services.get_policy(business))
        raw = request.query_params.get('date')
        day = parse_day(raw) if raw else timezone.now().astimezone(tz).date()
        return Response(build_overview(business, day))


class PerformanceView(AnalyticsMixin, APIView):
    """GET ?business=<id>&days=7|30|90, or &date_from=&date_to= for a custom range."""

    def get(self, request):
        from .overview import build_performance
        days = request.query_params.get('days')
        if days and not request.query_params.get('date_from'):
            try:
                days = int(days)
            except ValueError:
                raise ValidationError({'days': ['Must be a number of days.']})
            if not 1 <= days <= 366:
                raise ValidationError({'days': ['Pick between 1 and 366 days.']})
            business = self.get_business()
            tz = services.business_tz(services.get_policy(business))
            date_to = timezone.now().astimezone(tz).date()
            date_from = date_to - timedelta(days=days - 1)
        else:
            business, date_from, date_to, _ = self.report_args(request)
        return Response(build_performance(business, date_from, date_to))


class PaymentsView(AnalyticsMixin, APIView):
    """GET ?business=<id>&date_from=&date_to= (defaults to the last 30 days)."""

    def get(self, request):
        from .overview import build_payments
        business, date_from, date_to, _ = self.report_args(request)
        return Response(build_payments(business, date_from, date_to))
