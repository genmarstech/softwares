"""Customer accounts: session sign-in and joining businesses.

A signed-in customer is remembered in the Django session under
``SESSION_KEY``. This is separate from the business-owner login
(``django.contrib.auth``), so a salon owner testing their own booking page
is not mixed up with the customer they sign in as.
"""

from django.db import transaction
from django.db.models import Q
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from bookings.models import Booking, Customer

from .models import CustomerAccount
from .phones import normalize_phone, same_phone

SESSION_KEY = '_velora_customer_account'
SETUP_SHARE_TARGET = 5


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return reason


def enforce_csrf(request):
    """Customer writes ride on the session cookie, so they need the CSRF token too."""
    django_request = getattr(request, '_request', request)
    check = _CsrfCheck(lambda req: None)
    check.process_request(django_request)
    reason = check.process_view(django_request, None, (), {})
    if reason:
        raise PermissionDenied(f'CSRF Failed: {reason}')


def current_account(request):
    django_request = getattr(request, '_request', request)
    session = getattr(django_request, 'session', None)
    if session is None:
        return None
    account_id = session.get(SESSION_KEY)
    if not account_id:
        return None
    account = CustomerAccount.objects.filter(pk=account_id, is_active=True).first()
    if account is None:
        session.pop(SESSION_KEY, None)
    return account


def sign_in(request, account):
    django_request = getattr(request, '_request', request)
    session = django_request.session
    if session.get(SESSION_KEY) != account.pk:
        # New identity in this browser: issue a fresh session id (session fixation).
        session.cycle_key()
    session[SESSION_KEY] = account.pk
    account.last_login = timezone.now()
    account.save(update_fields=['last_login'])


def sign_out(request):
    django_request = getattr(request, '_request', request)
    django_request.session.pop(SESSION_KEY, None)


def find_account(phone_number):
    phone = normalize_phone(phone_number)
    if not phone:
        return None
    return CustomerAccount.objects.filter(phone_number=phone).first()


def membership(account, business):
    return Customer.objects.filter(business=business, account=account).first()


@transaction.atomic
def join_business(account, business, channel='link'):
    """Make ``account`` a customer of ``business`` and return its Customer record.

    An existing guest record (from bookings made before they had an account) is
    linked when the phone number matches and its email matches or is empty, so
    their earlier visits show up in their history. Phone numbers are not
    verified yet, so a phone match alone is not enough to see someone's
    bookings.
    """
    customer = membership(account, business)
    if customer is None:
        tail = ''.join(c for c in account.phone_number if c.isdigit())[-9:]
        email = account.email.lower()
        for guest in Customer.objects.select_for_update().filter(
            business=business, account__isnull=True, phone_number__endswith=tail,
        ):
            if not same_phone(guest.phone_number, account.phone_number):
                continue
            if guest.email and guest.email.lower() != email:
                continue
            customer = guest
            break
        if customer is None and email:
            # Same email, different or no phone: still the same person for this business
            # only if the record has no conflicting phone.
            guest = Customer.objects.select_for_update().filter(
                business=business, account__isnull=True, email__iexact=email,
            ).first()
            if guest and (not guest.phone_number or same_phone(guest.phone_number, account.phone_number)):
                customer = guest
        if customer is None:
            customer = Customer.objects.create(
                business=business, full_name=account.full_name, email=account.email,
                phone_number=account.phone_number, first_channel=channel,
            )
        customer.account = account
        if not customer.email and account.email:
            customer.email = account.email
        if not customer.phone_number:
            customer.phone_number = account.phone_number
    if customer.joined_at is None:
        customer.joined_at = timezone.now()
    customer.save()
    return customer


def account_payload(account):
    if account is None:
        return None
    return {
        'id': account.id,
        'full_name': account.full_name,
        'first_name': account.full_name.split(' ')[0],
        'phone_number': account.phone_number,
        'email': account.email,
    }


def reached_customers(business):
    """Customers who came through the booking link: joined with an account or booked online."""
    return Customer.objects.filter(business=business).filter(
        Q(joined_at__isnull=False) | Q(bookings__channel=Booking.Channel.ONLINE)
    ).distinct()
