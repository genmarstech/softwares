"""Customer accounts on a business's booking link (``/book/<slug>``).

Customers sign up or sign in with their phone number and a password. Doing so
on a business's link makes them a customer of that business, which counts
towards the business's "share your link" setup step. Their bookings then
appear in a history with one-tap rebooking.
"""

from django.db.models import Prefetch
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from bookings.models import Booking
from bookings.serializers import PublicBookingSerializer
from bookings.views import public_business

from . import services
from .models import CustomerAccount
from .serializers import LoginSerializer, ProfileSerializer, RegisterSerializer

INVALID_LOGIN = 'That phone number and password do not match an account.'


class CustomerAPIView(APIView):
    # Business-owner session auth is not used here: the customer is read from
    # the session by ``services.current_account``. Writes still check CSRF.
    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            services.enforce_csrf(request)

    def business_from(self, request):
        ref = (request.query_params.get('business') or request.data.get('business') or '').strip()
        if not ref:
            return None
        return public_business(ref)


def session_payload(request, account, business=None):
    customer = services.membership(account, business) if account and business else None
    return {
        'authenticated': account is not None,
        'account': services.account_payload(account),
        'joined': customer is not None,
        'csrfToken': get_token(request),
    }


@method_decorator(ensure_csrf_cookie, name='dispatch')
class SessionView(CustomerAPIView):
    """GET: who is signed in (and whether they joined ``?business=``). PATCH: update name or email."""

    def get(self, request):
        account = services.current_account(request)
        return Response(session_payload(request, account, self.business_from(request)))

    def patch(self, request):
        account = services.current_account(request)
        if account is None:
            return Response({'detail': 'Sign in first.'}, status=status.HTTP_401_UNAUTHORIZED)
        s = ProfileSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        for field, value in s.validated_data.items():
            setattr(account, field, value)
        account.save()
        # Keep each business's record in step where it still matches the account.
        for customer in account.memberships.all():
            if 'full_name' in s.validated_data:
                customer.full_name = account.full_name
            if s.validated_data.get('email') and not customer.email:
                customer.email = account.email
            customer.save(update_fields=['full_name', 'email', 'updated_at'])
        return Response(session_payload(request, account, self.business_from(request)))


class RegisterView(CustomerAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'customer_auth'

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        business = public_business(data['business'])
        account = CustomerAccount(
            phone_number=data['phone_number'], full_name=data['full_name'], email=data.get('email', ''),
        )
        account.set_password(data['password'])
        account.save()
        services.join_business(account, business)
        services.sign_in(request, account)
        return Response(session_payload(request, account, business), status=status.HTTP_201_CREATED)


class LoginView(CustomerAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'customer_auth'

    def post(self, request):
        s = LoginSerializer(data=request.data)
        if not s.is_valid():
            return Response({'detail': INVALID_LOGIN, **s.errors}, status=status.HTTP_400_BAD_REQUEST)
        data = s.validated_data
        business = public_business(data['business']) if data.get('business') else None
        account = services.find_account(data['phone_number'])
        if account is None or not account.is_active or not account.check_password(data['password']):
            return Response({'detail': INVALID_LOGIN}, status=status.HTTP_400_BAD_REQUEST)
        if business is not None:
            services.join_business(account, business)
        services.sign_in(request, account)
        return Response(session_payload(request, account, business))


class LogoutView(CustomerAPIView):
    def post(self, request):
        services.sign_out(request)
        return Response(session_payload(request, None))


class JoinView(CustomerAPIView):
    """A signed-in customer joins another business from its booking link."""

    def post(self, request):
        account = services.current_account(request)
        if account is None:
            return Response({'detail': 'Sign in first.'}, status=status.HTTP_401_UNAUTHORIZED)
        business = self.business_from(request)
        if business is None:
            return Response({'business': ['This field is required.']}, status=status.HTTP_400_BAD_REQUEST)
        services.join_business(account, business)
        return Response(session_payload(request, account, business))


class MyBookingsView(CustomerAPIView):
    """The signed-in customer's bookings at ``?business=``: upcoming first, then past visits.

    ``rebook`` on each past booking tells the booking page which service and
    team member to pre-select, so rebooking is one tap to a time.
    """

    def get(self, request):
        account = services.current_account(request)
        if account is None:
            return Response({'detail': 'Sign in first.'}, status=status.HTTP_401_UNAUTHORIZED)
        business = self.business_from(request)
        if business is None:
            return Response({'business': ['This query parameter is required.']},
                            status=status.HTTP_400_BAD_REQUEST)
        customer = services.membership(account, business)
        if customer is None:
            return Response({'joined': False, 'upcoming': [], 'past': [], 'favourites': []})

        bookings = list(
            Booking.objects.filter(customer=customer)
            .select_related('service', 'staff', 'customer', 'business')
            .prefetch_related(Prefetch('payments'))
            .order_by('-start')
        )
        now = timezone.now()
        upcoming = [b for b in bookings if b.end >= now and b.is_active]
        past = [b for b in bookings if b not in upcoming]
        upcoming.reverse()  # soonest first

        def with_rebook(booking):
            row = PublicBookingSerializer(booking).data
            service = booking.service
            staff = booking.staff
            row['rebook'] = {
                'available': bool(service.is_available),
                'service': service.id,
                'staff': staff.id if staff and staff.is_available else None,
            }
            return row

        # Services they come back for, most booked first (for the "Book again" row).
        favourites, seen = [], {}
        for b in bookings:
            if b.status in (Booking.Status.CANCELLED,):
                continue
            key = (b.service_id, b.staff_id)
            seen.setdefault(key, {'booking': b, 'count': 0})
            seen[key]['count'] += 1
        for item in sorted(seen.values(), key=lambda v: (-v['count'], -v['booking'].start.timestamp()))[:4]:
            b = item['booking']
            if not b.service.is_available:
                continue
            favourites.append({
                'service': b.service_id, 'service_name': b.service.service_name,
                'staff': b.staff_id if b.staff and b.staff.is_available else None,
                'staff_name': b.staff.staff_name if b.staff and b.staff.is_available else None,
                'price': str(b.service.price), 'duration_minutes': b.service.duration_minutes,
                'times_booked': item['count'], 'last_booked': b.start,
            })

        return Response({
            'joined': True,
            'upcoming': [with_rebook(b) for b in upcoming],
            'past': [with_rebook(b) for b in past[:50]],
            'favourites': favourites,
        })
