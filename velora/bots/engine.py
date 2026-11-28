"""The conversation, independent of Telegram or WhatsApp.

An adapter turns an incoming update into an ``Incoming`` and sends back the
``Reply`` objects ``handle`` returns. Buttons carry short action ids
(``svc:12``, ``day:20261201``...) that fit Telegram's 64-byte callback data and
WhatsApp's row ids. Lists never hold more than 10 options (WhatsApp's limit).
"""

import contextvars
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from bookings.models import Booking

from . import booking as svc
from .models import ChatIdentity

logger = logging.getLogger('velora.bots')

# While the bot is answering a chat, notifications about the same action to
# that same chat are skipped: the bot's own reply already says it.
quiet_chat = contextvars.ContextVar('quiet_chat', default=None)

MAX_OPTIONS = 10
SERVICES_PER_PAGE = 8
SLOTS_PER_PAGE = 8
DAYS_PER_PAGE = 6

GREETINGS = {'hi', 'hello', 'hey', 'menu', 'start', 'help', '/menu', '/help', 'habari', 'sasa', 'niaje'}
STOP_WORDS = {'stop', '/stop', 'unsubscribe', 'stop all'}
REF_RE = re.compile(r'\bref[:\s#]+([a-z0-9][a-z0-9-]{1,79})', re.IGNORECASE)


@dataclass
class Option:
    id: str
    title: str
    description: str = ''


@dataclass
class Reply:
    text: str
    options: list = field(default_factory=list)
    request_contact: bool = False
    remove_keyboard: bool = False
    link: tuple = None  # (label, url)


@dataclass
class Incoming:
    channel: str
    external_id: str
    text: str = ''
    action: str = ''          # id of the button the person tapped
    name: str = ''
    phone: str = ''           # a number the chat app vouches for
    contact_phone: str = ''   # a contact the person shared (Telegram)
    contact_is_self: bool = False


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def money(value):
    return f'KES {value:,.0f}'


def fmt_day(day):
    return day.strftime('%a %-d %b')


def fmt_when(business, dt):
    local = dt.astimezone(svc.local_tz(business))
    return local.strftime('%a %-d %b at %H:%M')


def booking_line(booking):
    staff = f' with {booking.staff.staff_name}' if booking.staff else ''
    return f'{booking.service.service_name}{staff}, {fmt_when(booking.business, booking.start)}'


def first_name(identity):
    if identity.account and identity.account.full_name:
        return identity.account.full_name.split(' ')[0]
    return (identity.display_name or 'there').split(' ')[0]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handle(incoming):
    identity, _ = ChatIdentity.objects.get_or_create(channel=incoming.channel, external_id=str(incoming.external_id))
    identity.last_inbound_at = timezone.now()
    if incoming.name:
        identity.display_name = incoming.name[:255]
    identity.save(update_fields=['last_inbound_at', 'display_name', 'updated_at'])

    token = quiet_chat.set((incoming.channel, str(incoming.external_id)))
    try:
        return Conversation(identity, incoming).run()
    except svc.BookingError as exc:
        return [Reply(exc.message, options=[Option('menu', 'Main menu')])]
    finally:
        quiet_chat.reset(token)


class Conversation:
    def __init__(self, identity, incoming):
        self.identity = identity
        self.incoming = incoming
        self.channel = incoming.channel
        self.text = (incoming.text or '').strip()
        self.action = incoming.action or ''
        self.state = dict(identity.state or {})
        self.replies = []

    # -- helpers -----------------------------------------------------------

    @property
    def business(self):
        return self.identity.business

    @property
    def account(self):
        return self.identity.account

    def say(self, text, options=None, **kwargs):
        options = list(options or [])[:MAX_OPTIONS]
        self.replies.append(Reply(text, options=options, **kwargs))

    def save(self, **fields):
        self.identity.state = self.state
        self.identity.save(update_fields=['state', 'updated_at', *fields.keys()])

    def set_business(self, business):
        if business is None:
            return
        if self.identity.business_id != business.id:
            self.state = {}
        self.identity.business = business
        self.identity.save(update_fields=['business', 'updated_at'])

    # -- main flow ----------------------------------------------------------

    def run(self):
        lowered = self.text.lower()

        if lowered in STOP_WORDS:
            self.identity.opted_out = True
            self.identity.save(update_fields=['opted_out', 'updated_at'])
            self.say('You will not get reminders or updates here any more. Send START to turn them back on.')
            return self.replies
        if self.identity.opted_out and lowered in ('start', '/start'):
            self.identity.opted_out = False
            self.identity.save(update_fields=['opted_out', 'updated_at'])
            self.say('Reminders and updates are back on.')

        # Telegram deep link: /start <slug> or /start o_<owner token>
        if lowered.startswith('/start'):
            param = self.text[6:].strip()
            if param.startswith('o_'):
                return self.link_owner(param[2:])
            if param:
                self.set_business(svc.find_business(param))
            self.action = self.action or 'menu'
        else:
            ref = REF_RE.search(self.text)
            if ref:
                self.set_business(svc.find_business(ref.group(1)))
                self.action = 'menu'

        # Owner buttons on a new-booking alert.
        if self.action.startswith(('oc:', 'od:')):
            return self.owner_decision()

        # A business named in the button (from a reminder or follow-up message).
        if ':' in self.action and self.action.split(':')[0] in ('again', 'mine', 'menu', 'new'):
            name, ref = self.action.split(':', 1)
            self.set_business(svc.find_business(ref))
            self.action = name
        if self.action.startswith('biz:'):
            self.set_business(svc.find_business(self.action[4:]))
            self.action = 'menu'

        if not self.ensure_account():
            return self.replies

        if self.business is None:
            return self.choose_business()

        if not svc.channel_enabled(self.business, self.channel):
            self.say(f'{self.business.business_name} does not take bookings here yet. '
                     f'You can book online instead.', link=('Book online', svc.booking_link(self.business)))
            return self.replies

        svc.customer_for(self.account, self.business, self.channel)
        self.route()
        return self.replies

    def ensure_account(self):
        if self.account is not None:
            return True
        phone = self.incoming.phone
        if not phone and self.incoming.contact_phone:
            if not self.incoming.contact_is_self:
                self.say('Please share your own number with the button below.', request_contact=True)
                return False
            phone = self.incoming.contact_phone
        if phone:
            account = svc.link_account(self.identity, phone, self.incoming.name or self.identity.display_name)
            if self.incoming.contact_phone:
                self.say(f'Thanks, {account.full_name.split(" ")[0]}! Your number is linked.', remove_keyboard=True)
                self.action = self.action or 'menu'
            return True
        where = f' to book with {self.business.business_name}' if self.business else ''
        self.say(f'Welcome to Velora! Please share your phone number{where}. '
                 'It is how the business knows it is you, and it keeps your bookings in one place.',
                 request_contact=True)
        return False

    def choose_business(self):
        businesses = svc.joined_businesses(self.account)
        if businesses:
            self.say('Which business would you like to book with?',
                     [Option(f'biz:{b.slug}', b.business_name) for b in businesses[:MAX_OPTIONS]])
        else:
            self.say('Open your salon\'s Velora booking link to start, or send its booking reference, '
                     'like "ref: glow-salon".')
        return self.replies

    def route(self):
        action = self.action
        name, _, arg = action.partition(':')
        handlers = {
            'menu': self.menu, 'new': self.choose_service, 'svcs': self.choose_service,
            'again': self.book_again, 'svc': self.pick_service, 'stf': self.pick_staff,
            'days': self.choose_day, 'day': self.choose_slot, 'slots': self.choose_slot,
            'slot': self.pick_slot, 'ok': self.confirm, 'mine': self.my_bookings, 'bk': self.show_booking,
            'cx': self.ask_cancel, 'cxy': self.do_cancel, 'rs': self.start_reschedule,
        }
        handler = handlers.get(name)
        if handler is None:
            if self.text and self.text.lower() not in GREETINGS and not self.text.lower().startswith('/start'):
                self.say('Sorry, I did not catch that. Pick an option below.')
            return self.menu('')
        return handler(arg)

    # -- menu -----------------------------------------------------------------

    def menu(self, _arg=''):
        self.state = {}
        self.save()
        b = self.business
        options = []
        last = svc.last_visit(self.account, b)
        if last is not None:
            staff = f' with {last.staff.staff_name.split(" ")[0]}' if last.staff and last.staff.is_available else ''
            options.append(Option('again', 'Book again', f'{last.service.service_name}{staff}'))
        options.append(Option('new', 'New booking', 'Choose a service and time'))
        if svc.upcoming(self.account, b):
            options.append(Option('mine', 'My bookings', 'See, move or cancel'))
        self.say(f'Hi {first_name(self.identity)}, welcome to {b.business_name}. What would you like to do?',
                 options)

    # -- booking --------------------------------------------------------------

    def choose_service(self, arg=''):
        services = svc.services_for(self.business)
        if not services:
            self.say(f'{self.business.business_name} has not listed any services yet.',
                     link=('Visit their page', svc.booking_link(self.business)))
            return
        page = int(arg) if arg.isdigit() else 0
        chunk = services[page * SERVICES_PER_PAGE:(page + 1) * SERVICES_PER_PAGE]
        options = [Option(f'svc:{s.id}', s.service_name, f'{money(s.price)} · {s.duration_minutes} min') for s in chunk]
        if len(services) > (page + 1) * SERVICES_PER_PAGE:
            options.append(Option(f'svcs:{page + 1}', 'More services'))
        options.append(Option('menu', 'Main menu'))
        self.state = {'mode': 'book'}
        self.save()
        self.say('Which service would you like?', options)

    def book_again(self, _arg=''):
        last = svc.last_visit(self.account, self.business)
        if last is None:
            return self.choose_service()
        staff = last.staff if last.staff and last.staff.is_available else None
        self.state = {'mode': 'book', 'svc': last.service_id, 'stf': staff.id if staff else None}
        self.save()
        return self.choose_day('')

    def pick_service(self, arg):
        service = svc.service(self.business, arg)
        if service is None:
            self.say('That service is no longer available.')
            return self.choose_service()
        self.state.update({'mode': 'book', 'svc': service.id, 'stf': None})
        self.save()
        team = svc.staff_for(self.business, service)
        if len(team) > 1:
            options = [Option('stf:0', 'Anyone available')]
            options += [Option(f'stf:{m.id}', m.staff_name) for m in team[:MAX_OPTIONS - 2]]
            options.append(Option('menu', 'Main menu'))
            self.say(f'{service.service_name}: who would you like?', options)
            return
        return self.choose_day('')

    def pick_staff(self, arg):
        member = svc.staff_member(self.business, int(arg)) if arg.isdigit() and arg != '0' else None
        self.state['stf'] = member.id if member else None
        self.save()
        return self.choose_day('')

    def _service_and_staff(self):
        service = svc.service(self.business, self.state.get('svc'))
        if service is None:
            return None, None
        return service, svc.staff_member(self.business, self.state.get('stf'))

    def choose_day(self, arg):
        service, staff = self._service_and_staff()
        if service is None:
            return self.choose_service()
        start = _parse_day(arg) if arg else None
        days = svc.open_days(self.business, service, staff, start=start, limit=DAYS_PER_PAGE)
        who = f' with {staff.staff_name}' if staff else ''
        if not days:
            self.say(f'There are no open times for {service.service_name}{who} in the next few weeks.',
                     [Option('new', 'Pick another service'), Option('menu', 'Main menu')])
            return
        options = [Option(f'day:{d:%Y%m%d}', fmt_day(d)) for d in days]
        if len(days) == DAYS_PER_PAGE:
            next_day = days[-1].toordinal() + 1
            options.append(Option(f'days:{datetime.fromordinal(next_day):%Y%m%d}', 'Later dates'))
        options.append(Option('menu', 'Main menu'))
        self.say(f'{service.service_name}{who}: which day suits you?', options)

    def choose_slot(self, arg):
        service, staff = self._service_and_staff()
        if service is None:
            return self.choose_service()
        day_part, _, page_part = arg.partition(':')
        day = _parse_day(day_part)
        if day is None:
            return self.choose_day('')
        page = int(page_part) if page_part.isdigit() else 0
        slots = svc.open_slots(self.business, service, day, staff, exclude_booking_id=self.state.get('bk'))
        if not slots:
            self.say(f'{fmt_day(day)} just filled up.')
            return self.choose_day('')
        tz = svc.local_tz(self.business)
        chunk = slots[page * SLOTS_PER_PAGE:(page + 1) * SLOTS_PER_PAGE]
        options = [Option(f'slot:{s["start"].astimezone(tz):%Y%m%d%H%M}', s['start'].astimezone(tz).strftime('%H:%M'))
                   for s in chunk]
        if len(slots) > (page + 1) * SLOTS_PER_PAGE:
            options.append(Option(f'slots:{day:%Y%m%d}:{page + 1}', 'Later times'))
        options.append(Option('days', 'Another day'))
        self.say(f'Open times on {fmt_day(day)}:', options)

    def pick_slot(self, arg):
        service, staff = self._service_and_staff()
        start = _parse_start(arg, self.business)
        if service is None or start is None:
            return self.menu()
        self.state['start'] = start.isoformat()
        self.save()
        who = f' with {staff.staff_name}' if staff else ''
        if self.state.get('mode') == 'reschedule':
            text = f'Move your booking to {fmt_when(self.business, start)}?'
            ok = 'Yes, move it'
        else:
            text = (f'Please confirm:\n{service.service_name}{who}\n{fmt_when(self.business, start)}\n'
                    f'{money(service.price)} · {service.duration_minutes} min')
            ok = 'Confirm booking'
        self.say(text, [Option('ok', ok), Option(f'day:{start.astimezone(svc.local_tz(self.business)):%Y%m%d}',
                                                    'Pick another time'), Option('menu', 'Start over')])

    def confirm(self, _arg=''):
        service, staff = self._service_and_staff()
        start = _iso(self.state.get('start'))
        if service is None or start is None:
            return self.menu()
        if self.state.get('mode') == 'reschedule':
            booking = svc.reschedule(self.account, self.state.get('bk'), start)
            self.state = {}
            self.save()
            self.say(f'Done! Your booking is now {booking_line(booking)}.',
                     [Option('mine', 'My bookings'), Option('menu', 'Main menu')])
            return
        booking = svc.book(self.account, self.business, service, start, staff, self.channel)
        self.state = {}
        self.save()
        if booking.status == Booking.Status.CONFIRMED:
            text = f'You are booked! {booking_line(booking)}. See you then.'
        elif booking.status == Booking.Status.HELD:
            text = (f'We are holding {booking_line(booking)} for you. Please pay the deposit of '
                    f'{money(booking.deposit_amount)} to confirm it. {self.business.business_name} will share how to pay.')
        else:
            text = (f'Request sent: {booking_line(booking)}. {self.business.business_name} will confirm it shortly, '
                    'and I will message you here.')
        self.say(text, [Option('mine', 'My bookings'), Option('menu', 'Main menu')])

    # -- existing bookings ------------------------------------------------------

    def my_bookings(self, _arg=''):
        bookings = svc.upcoming(self.account, self.business)
        if not bookings:
            self.say('You have no upcoming bookings here.',
                     [Option('new', 'New booking'), Option('menu', 'Main menu')])
            return
        options = [Option(f'bk:{b.id}', fmt_when(self.business, b.start)[:24],
                          f'{b.service.service_name} · {b.get_status_display()}') for b in bookings[:MAX_OPTIONS - 1]]
        options.append(Option('menu', 'Main menu'))
        self.say('Your upcoming bookings:', options)

    def show_booking(self, arg):
        booking = svc.customer_booking(self.account, arg)
        if booking is None or booking.business_id != self.business.id:
            return self.my_bookings()
        options = []
        if booking.is_active:
            options += [Option(f'rs:{booking.id}', 'Reschedule'), Option(f'cx:{booking.id}', 'Cancel')]
        options.append(Option('mine', 'Back'))
        self.say(f'{booking_line(booking)}\nStatus: {booking.get_status_display()}', options)

    def ask_cancel(self, arg):
        booking = svc.customer_booking(self.account, arg)
        if booking is None:
            return self.my_bookings()
        self.say(f'Cancel {booking_line(booking)}?',
                 [Option(f'cxy:{booking.id}', 'Yes, cancel it'), Option('mine', 'Keep it')])

    def do_cancel(self, arg):
        booking = svc.cancel(self.account, arg)
        fee = f' A late cancellation fee of {money(booking.cancellation_fee)} applies.' if booking.cancellation_fee else ''
        self.say(f'Cancelled: {booking_line(booking)}.{fee}',
                 [Option('new', 'Book another time'), Option('menu', 'Main menu')])

    def start_reschedule(self, arg):
        booking = svc.customer_booking(self.account, arg)
        if booking is None or not booking.is_active:
            return self.my_bookings()
        self.state = {'mode': 'reschedule', 'bk': booking.id, 'svc': booking.service_id, 'stf': booking.staff_id}
        self.save()
        return self.choose_day('')

    # -- owner --------------------------------------------------------------

    def link_owner(self, token):
        bot = svc.link_owner_telegram(token, self.identity.external_id,
                                      self.incoming.name or self.identity.display_name)
        if bot is None:
            self.say('That link has expired. Open the Channels page in your Velora dashboard for a new one.')
        else:
            self.say(f'Done! New bookings for {bot.business.business_name} will arrive here, '
                     'with buttons to confirm or decline them.')
        return self.replies

    def owner_decision(self):
        name, _, booking_id = self.action.partition(':')
        booking = svc.owner_decide(self.channel, self.identity.external_id, booking_id, accept=(name == 'oc'))
        verb = 'Confirmed' if booking.status == Booking.Status.CONFIRMED else 'Declined'
        if booking.status not in (Booking.Status.CONFIRMED, Booking.Status.CANCELLED):
            verb = booking.get_status_display()
        self.say(f'{verb}: {booking.customer.full_name}, {booking_line(booking)}. The customer has been told.')
        return self.replies


def _parse_day(value):
    try:
        return datetime.strptime(value, '%Y%m%d').date()
    except (TypeError, ValueError):
        return None


def _parse_start(value, business):
    try:
        naive = datetime.strptime(value, '%Y%m%d%H%M')
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=svc.local_tz(business))


def _iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
