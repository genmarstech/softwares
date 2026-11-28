"""Booking service the chat bots share.

Everything a bot can do (find a business by its link, list services and
open times, book, rebook the last visit, cancel, reschedule) goes through here
and on into ``bookings.services``, so the same rules apply as on the web
booking page. The Telegram and WhatsApp adapters only translate messages.
"""

from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from bookings import services as booking_services
from bookings.models import Booking, BookingEvent
from business.models import Business
from customers.models import CustomerAccount
from customers.phones import normalize_phone
from customers.services import join_business, membership

from .models import BotSettings, ChatIdentity

BookingError = booking_services.BookingError


# ---------------------------------------------------------------------------
# Businesses and their bot settings
# ---------------------------------------------------------------------------

def find_business(ref):
    """A business by booking-link slug or numeric id, or None."""
    ref = str(ref or '').strip().lower()
    if not ref:
        return None
    if ref.isdigit():
        return Business.objects.filter(pk=int(ref)).first()
    return Business.objects.filter(slug=ref).first()


def settings_for(business):
    obj, _ = BotSettings.objects.get_or_create(business=business)
    return obj


def channel_enabled(business, channel):
    return settings_for(business).enabled(channel)


def booking_link(business):
    return f'{settings.FRONTEND_URL}/book/{business.slug or business.id}'


def telegram_link(business):
    username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').lstrip('@')
    return f'https://t.me/{username}?start={business.slug}' if username else ''


def whatsapp_link(business):
    number = ''.join(c for c in getattr(settings, 'WHATSAPP_DISPLAY_NUMBER', '') if c.isdigit())
    if not number:
        return ''
    text = f'Hi {business.business_name}! I would like to book. (ref: {business.slug})'
    return f'https://wa.me/{number}?text={quote(text)}'


def owner_telegram_link(bot_settings):
    username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').lstrip('@')
    if not username:
        return ''
    token = bot_settings.owner_link_token or bot_settings.new_owner_link_token()
    return f'https://t.me/{username}?start=o_{token}'


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@transaction.atomic
def link_account(identity, phone_number, name=''):
    """Attach the verified ``phone_number`` to the chat and its CustomerAccount.

    The chat app has proved the number belongs to this person, so the account
    is found by it (or created with no password: they can still book on the
    web as a guest, and a web password can come later).
    """
    phone = normalize_phone(phone_number)
    if not phone:
        raise BookingError('That phone number does not look right.')
    account = CustomerAccount.objects.select_for_update().filter(phone_number=phone).first()
    if account is None:
        account = CustomerAccount(phone_number=phone, full_name=(name or 'Customer').strip()[:255])
        account.set_password(None)  # unusable until they set one on the web
    if account.phone_verified_at is None:
        account.phone_verified_at = timezone.now()
    account.save()
    identity.phone_number = phone
    identity.account = account
    identity.save(update_fields=['phone_number', 'account', 'updated_at'])
    return account


def customer_for(account, business, channel):
    customer = membership(account, business)
    if customer is None or customer.joined_at is None:
        customer = join_business(account, business, channel=channel)
    return customer


def joined_businesses(account):
    return list(Business.objects.filter(customers__account=account).order_by('business_name').distinct())


# ---------------------------------------------------------------------------
# Services, team and open times
# ---------------------------------------------------------------------------

def services_for(business):
    # Same rule as the booking link: only services the business is set up to take online.
    blocked = set(booking_services.booking_readiness(business)['unbookable_service_ids'])
    return [s for s in business.services.filter(is_available=True).order_by('service_name') if s.id not in blocked]


def service(business, service_id):
    return business.services.filter(pk=service_id, is_available=True).first()


def staff_for(business, svc):
    return booking_services.eligible_staff(business, svc)


def staff_member(business, staff_id):
    if not staff_id:
        return None
    return business.business_staff.filter(pk=staff_id, is_available=True).first()


def local_tz(business):
    return booking_services.business_tz(booking_services.get_policy(business))


def today(business):
    return timezone.now().astimezone(local_tz(business)).date()


def open_days(business, svc, staff=None, start=None, limit=6, search_days=21):
    """Next local dates (from ``start``) with at least one open slot."""
    policy = booking_services.get_policy(business)
    first = max(start or today(business), today(business))
    last = today(business) + timedelta(days=policy.max_advance_days)
    days = []
    day = first
    while day <= last and len(days) < limit and (day - first).days < search_days:
        if booking_services.get_available_slots(business, svc, day, staff=staff):
            days.append(day)
        day += timedelta(days=1)
    return days


def open_slots(business, svc, day, staff=None, exclude_booking_id=None):
    return booking_services.get_available_slots(business, svc, day, staff=staff,
                                                exclude_booking_id=exclude_booking_id)


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def book(account, business, svc, start, staff, channel, notes=''):
    if booking_services.service_readiness(business, svc):
        raise booking_services.BookingError(
            f'{business.business_name} is not taking online bookings for this service yet.', code='not_ready')
    customer = customer_for(account, business, channel)
    return booking_services.create_booking(
        business=business, service=svc, customer=customer, start=start, staff=staff,
        channel=channel, intake_notes=notes, actor_type=BookingEvent.Actor.CUSTOMER,
    )


def last_visit(account, business):
    """The booking to repeat with "Book again": the latest one that was not cancelled."""
    customer = membership(account, business)
    if customer is None:
        return None
    for b in (Booking.objects.filter(customer=customer).exclude(status=Booking.Status.CANCELLED)
              .select_related('service', 'staff').order_by('-start')[:10]):
        if b.service.is_available:
            return b
    return None


def upcoming(account, business):
    customer = membership(account, business)
    if customer is None:
        return []
    return [
        b for b in Booking.objects.filter(customer=customer, end__gte=timezone.now())
        .select_related('service', 'staff').order_by('start')
        if b.is_active
    ]


def customer_booking(account, booking_id):
    """One of the account's own bookings, or None."""
    try:
        booking_id = int(booking_id)
    except (TypeError, ValueError):
        return None
    return (Booking.objects.filter(pk=booking_id, customer__account=account)
            .select_related('business', 'service', 'staff', 'customer').first())


def cancel(account, booking_id):
    booking = customer_booking(account, booking_id)
    if booking is None:
        raise BookingError('That booking was not found.')
    return booking_services.cancel_booking(booking, actor_type=BookingEvent.Actor.CUSTOMER,
                                           reason='Cancelled in chat')


def reschedule(account, booking_id, start):
    booking = customer_booking(account, booking_id)
    if booking is None:
        raise BookingError('That booking was not found.')
    return booking_services.reschedule_booking(booking, start=start, staff=booking.staff,
                                               actor_type=BookingEvent.Actor.CUSTOMER)


# ---------------------------------------------------------------------------
# Owner actions from chat (Confirm / Decline on a new-booking alert)
# ---------------------------------------------------------------------------

def is_owner_chat(business, channel, external_id):
    bot = settings_for(business)
    if channel == 'telegram':
        return bool(bot.owner_telegram_chat_id) and bot.owner_telegram_chat_id == str(external_id)
    if channel == 'whatsapp':
        owner_phone = normalize_phone(business.whatsapp_number)
        return bot.alert_owner_whatsapp and bool(owner_phone) and owner_phone == normalize_phone(f'+{external_id}')
    return False


def owner_decide(channel, external_id, booking_id, accept):
    try:
        booking = Booking.objects.select_related('business', 'customer', 'service', 'staff').get(pk=int(booking_id))
    except (Booking.DoesNotExist, TypeError, ValueError):
        raise BookingError('That booking was not found.')
    if not is_owner_chat(booking.business, channel, external_id):
        raise BookingError('Only the business can confirm or decline bookings.')
    if accept:
        return booking_services.confirm_booking(booking, actor_type=BookingEvent.Actor.OWNER)
    return booking_services.cancel_booking(booking, actor_type=BookingEvent.Actor.OWNER,
                                           reason='Declined by the business in chat', override=True)


def link_owner_telegram(token, chat_id, name):
    if not token:
        return None
    bot = BotSettings.objects.filter(owner_link_token=token).select_related('business').first()
    if bot is None:
        return None
    bot.owner_telegram_chat_id = str(chat_id)
    bot.owner_telegram_name = name[:255]
    bot.owner_link_token = ''  # single use
    bot.save()
    return bot


def chat_addresses(business, customer):
    """Bot chats that can receive this customer's updates: ``{"telegram": chat_id, "whatsapp": number}``."""
    if customer.account_id is None:
        return {}
    bot = BotSettings.objects.filter(business=business).first()
    if bot is None:
        return {}
    result = {}
    for identity in ChatIdentity.objects.filter(account_id=customer.account_id, opted_out=False).order_by('-updated_at'):
        if identity.channel in result or not bot.enabled(identity.channel):
            continue
        result[identity.channel] = identity.external_id
    return result


def owner_chat_targets(business):
    """Where the business gets booking alerts in chat: ``[(channel, address, name)]``."""
    bot = BotSettings.objects.filter(business=business).first()
    if bot is None:
        return []
    targets = []
    if bot.owner_telegram_chat_id:
        targets.append(('telegram', bot.owner_telegram_chat_id, bot.owner_telegram_name or business.business_name))
    owner_phone = normalize_phone(business.whatsapp_number)
    if bot.alert_owner_whatsapp and owner_phone:
        targets.append(('whatsapp', owner_phone.lstrip('+'), business.business_name))
    return targets
