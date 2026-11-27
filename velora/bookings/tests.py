import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from business.models import Business, BusinessStaff, Service
from notifications.models import Notification

from . import services
from .models import Booking, BookingEvent, Customer, Payment, TimeOff, WaitlistEntry, WorkingHours

User = get_user_model()
NAIROBI = ZoneInfo('Africa/Nairobi')


def next_weekday(weekday, weeks_ahead=1):
    today = timezone.now().astimezone(NAIROBI).date()
    days = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days + 7 * (weeks_ahead - 1))


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=NAIROBI)


class VeloraTestCase(TestCase):
    def setUp(self):
        logging.disable(logging.INFO)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.owner = User.objects.create_user('owner@example.com', 'pass-12345', full_name='Owner One')
        self.other = User.objects.create_user('other@example.com', 'pass-12345', full_name='Owner Two')
        self.business = Business.objects.create(
            business_owner=self.owner, business_name='Glow Salon', business_description='Hair',
            whatsapp_number='+254700000000', business_email='glow@example.com',
        )
        self.other_business = Business.objects.create(
            business_owner=self.other, business_name='Other Spa', business_description='Spa',
            business_category='SP', whatsapp_number='+254700000001', business_email='spa@example.com',
        )
        services.ensure_business_defaults(self.business)
        services.ensure_business_defaults(self.other_business)
        self.policy = services.get_policy(self.business)
        self.policy.min_notice_minutes = 0
        self.policy.save()
        self.service = Service.objects.create(
            business=self.business, service_name='Haircut', price=Decimal('1000'), duration_minutes=60,
        )
        self.staff = BusinessStaff.objects.create(
            business=self.business, staff_name='Amina', staff_email='amina@example.com', id_number='1234',
        )
        self.customer = Customer.objects.create(
            business=self.business, full_name='Jane Doe', email='jane@example.com', phone_number='+254711111111',
        )
        self.monday = next_weekday(0)
        self.client = APIClient()

    def login(self, user=None):
        self.client.force_authenticate(user or self.owner)

    def book(self, start, **kwargs):
        params = dict(business=self.business, service=self.service, customer=self.customer, start=start,
                      staff=self.staff)
        params.update(kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            return services.create_booking(**params)


class TenantIsolationTests(VeloraTestCase):
    def test_services_list_is_scoped_to_the_owners_business(self):
        Service.objects.create(business=self.other_business, service_name='Massage', price=10, duration_minutes=30)
        self.login()
        response = self.client.get('/bn/services/', {'business': self.business.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s['service_name'] for s in response.json()], ['Haircut'])

    def test_cannot_read_another_owners_business_data(self):
        self.login(self.other)
        for url in ('/bn/services/', '/bn/staff/', '/bk/bookings/', '/bk/customers/', '/bk/policy/'):
            response = self.client.get(url, {'business': self.business.id})
            self.assertEqual(response.status_code, 404, url)

    def test_cannot_open_another_owners_booking_by_id(self):
        booking = self.book(at(self.monday, 10))
        self.login(self.other)
        self.assertEqual(self.client.get(f'/bk/bookings/{booking.id}/').status_code, 404)
        self.assertEqual(self.client.post(f'/bk/bookings/{booking.id}/cancel/').status_code, 404)

    def test_cannot_create_service_for_another_business(self):
        self.login(self.other)
        response = self.client.post('/bn/services/', {
            'business': self.business.id, 'service_name': 'Sneaky', 'price': '1', 'duration_minutes': 30,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('business', response.json())

    def test_list_requires_business_param(self):
        self.login()
        self.assertEqual(self.client.get('/bk/bookings/').status_code, 400)

    def test_anonymous_cannot_list_services(self):
        self.assertIn(self.client.get('/bn/services/').status_code, (401, 403))


class AuthAndCsrfTests(TestCase):
    def test_csrf_endpoint_sets_cookie_and_session_writes_need_token(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.post('/idn/api/auth/register/', {
            'full_name': 'New Owner', 'email': 'New@Example.com', 'phone_number': '+254700', 'password': 'Velora!2345',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['user']['email'], 'new@example.com')
        self.assertTrue(body['csrfToken'])

        payload = {'business_name': 'X', 'business_description': 'd', 'business_category': 'SC',
                   'whatsapp_number': '+254 712 345 678', 'business_email': 'x@example.com'}
        self.assertEqual(client.post('/bn/business/', payload, format='json').status_code, 403)

        token = client.get('/idn/api/auth/csrf/').json()['csrfToken']
        response = client.post('/bn/business/', payload, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.content)
        # A new business gets default policy and hours.
        business = Business.objects.get(pk=response.json()['id'])
        self.assertTrue(WorkingHours.objects.filter(business=business).exists())

    def test_login_returns_user_and_token(self):
        User.objects.create_user('a@example.com', 'pass-12345', full_name='A')
        response = APIClient().post('/idn/api/auth/login/', {'email': 'A@example.com ', 'password': 'pass-12345'},
                                    format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['name'], 'A')
        self.assertTrue(response.json()['csrfToken'])


class AvailabilityTests(VeloraTestCase):
    def test_slots_follow_working_hours(self):
        slots = services.get_available_slots(self.business, self.service, self.monday)
        self.assertEqual(slots[0]['start'], at(self.monday, 9))
        self.assertEqual(slots[-1]['start'], at(self.monday, 17))

    def test_closed_day_has_no_slots(self):
        self.assertEqual(services.get_available_slots(self.business, self.service, next_weekday(6)), [])

    def test_breaks_bookings_buffers_and_time_off_block_slots(self):
        hours = WorkingHours.objects.get(business=self.business, staff__isnull=True, weekday=0)
        hours.break_start, hours.break_end = time(13), time(14)
        hours.save()
        self.service.buffer_after_minutes = 15
        self.service.save()
        self.book(at(self.monday, 10))
        TimeOff.objects.create(business=self.business, staff=self.staff, start=at(self.monday, 16),
                               end=at(self.monday, 18))
        starts = {s['start'] for s in services.get_available_slots(self.business, self.service, self.monday)}
        self.assertNotIn(at(self.monday, 10), starts)
        self.assertNotIn(at(self.monday, 11), starts)        # 10:00 booking + 15 min cleanup
        self.assertIn(at(self.monday, 11, 15), starts)
        self.assertNotIn(at(self.monday, 12, 30), starts)    # would run into the break
        self.assertIn(at(self.monday, 14), starts)
        self.assertNotIn(at(self.monday, 15, 30), starts)    # time off from 16:00

    def test_staff_skills_limit_who_is_offered(self):
        colour = Service.objects.create(business=self.business, service_name='Colour', price=50, duration_minutes=30)
        self.staff.skills.add(colour)
        self.assertEqual(services.get_available_slots(self.business, self.service, self.monday), [])

    def test_max_advance_window(self):
        self.policy.max_advance_days = 3
        self.policy.save()
        far = timezone.now().astimezone(NAIROBI).date() + timedelta(days=10)
        self.assertEqual(services.get_available_slots(self.business, self.service, far), [])


class BookingEngineTests(VeloraTestCase):
    def test_double_booking_is_prevented(self):
        self.book(at(self.monday, 10))
        with self.assertRaises(services.BookingError) as ctx:
            self.book(at(self.monday, 10, 30))
        self.assertEqual(ctx.exception.code, 'conflict')

    def test_any_staff_picks_a_free_member(self):
        second = BusinessStaff.objects.create(business=self.business, staff_name='Ben', staff_email='b@x.com',
                                              id_number='999')
        self.book(at(self.monday, 10))
        booking = self.book(at(self.monday, 10), staff=None)
        self.assertEqual(booking.staff, second)

    def test_outside_working_hours_rejected(self):
        with self.assertRaises(services.BookingError):
            self.book(at(self.monday, 7))

    def test_lifecycle_is_logged(self):
        booking = self.book(at(self.monday, 10))
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        with self.captureOnCommitCallbacks(execute=True):
            services.reschedule_booking(booking, start=at(self.monday, 12), actor_type=BookingEvent.Actor.OWNER)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.RESCHEDULED)
        self.assertEqual(booking.start, at(self.monday, 12))
        with mock.patch('django.utils.timezone.now', return_value=at(self.monday, 13, 5)):
            services.complete_booking(booking)
        actions = list(booking.events.order_by('created_at', 'id').values_list('action', flat=True))
        self.assertEqual(actions, ['created', 'rescheduled', 'completed'])

    def test_deposit_hold_then_payment_confirms(self):
        self.policy.deposit_required = True
        self.policy.deposit_percentage = Decimal('20')
        self.policy.save()
        booking = self.book(at(self.monday, 10), channel=Booking.Channel.ONLINE)
        self.assertEqual(booking.status, Booking.Status.HELD)
        self.assertEqual(booking.deposit_amount, Decimal('200.00'))
        with self.captureOnCommitCallbacks(execute=True):
            services.record_payment(booking, amount=Decimal('200'), kind=Payment.Kind.DEPOSIT)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.balance_due, Decimal('800.00'))

    def test_expired_hold_frees_the_slot(self):
        self.policy.deposit_required = True
        self.policy.deposit_percentage = Decimal('10')
        self.policy.save()
        booking = self.book(at(self.monday, 10))
        Booking.objects.filter(pk=booking.pk).update(hold_expires_at=timezone.now() - timedelta(minutes=1))
        starts = {s['start'] for s in services.get_available_slots(self.business, self.service, self.monday)}
        self.assertIn(at(self.monday, 10), starts)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.expire_holds(), 1)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_no_show_charges_fee(self):
        self.policy.cancellation_fee_percentage = Decimal('50')
        self.policy.save()
        booking = self.book(at(self.monday, 10))
        with mock.patch('django.utils.timezone.now', return_value=at(self.monday, 11)):
            services.mark_no_show(booking)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.NO_SHOW)
        self.assertEqual(booking.cancellation_fee, Decimal('500.00'))

    def test_walk_in_from_dashboard(self):
        self.login()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/bk/bookings/', {
            'business': self.business.id, 'service': self.service.id, 'staff': self.staff.id,
                'customer_name': 'Walk In', 'customer_phone': '+254722222222', 'channel': 'walk_in',
                'start': at(self.monday, 9).isoformat(),
            }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['status'], 'confirmed')
        self.assertTrue(Notification.objects.filter(event='staff_walk_in').exists())


class PublicFlowTests(VeloraTestCase):
    def test_customer_books_views_and_cancels(self):
        client = APIClient()
        info = client.get(f'/bk/public/{self.business.id}/').json()
        self.assertEqual(info['services'][0]['service_name'], 'Haircut')
        self.assertNotIn('staff_email', info['staff'][0])

        slots = client.get(f'/bk/public/{self.business.id}/availability/',
                           {'service': self.service.id, 'date': self.monday.isoformat()}).json()['slots']
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(f'/bk/public/{self.business.id}/bookings/', {
                'service': self.service.id, 'start': slots[0]['start'], 'full_name': 'Sam Client',
                'email': 'sam@example.com', 'phone_number': '+254733333333', 'intake_notes': 'Sensitive scalp',
            }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        token = response.json()['manage_token']

        manage = client.get(f'/bk/public/manage/{token}/').json()
        self.assertTrue(manage['can_cancel'])
        with self.captureOnCommitCallbacks(execute=True):
            cancelled = client.post(f'/bk/public/manage/{token}/cancel/', {'reason': 'Busy'}, format='json')
        self.assertEqual(cancelled.json()['status'], 'cancelled')

    def test_customer_cannot_cancel_inside_window(self):
        soon = timezone.now() + timedelta(hours=3)
        booking = self.book(soon.replace(second=0, microsecond=0), enforce_policy=False,
                            channel=Booking.Channel.WALK_IN)
        response = APIClient().post(f'/bk/public/manage/{booking.manage_token}/cancel/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'policy')

    def test_public_double_booking_returns_conflict(self):
        self.book(at(self.monday, 10))
        response = APIClient().post(f'/bk/public/{self.business.id}/bookings/', {
            'service': self.service.id, 'staff': self.staff.id, 'start': at(self.monday, 10).isoformat(),
            'full_name': 'Late Comer', 'email': 'late@example.com',
        }, format='json')
        self.assertEqual(response.status_code, 409)

    def test_customer_reschedules(self):
        booking = self.book(at(self.monday, 10))
        response = APIClient().post(f'/bk/public/manage/{booking.manage_token}/reschedule/',
                                    {'start': at(self.monday, 15).isoformat()}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['reschedule_count'], 1)

    def test_waitlist_notified_on_cancellation(self):
        booking = self.book(at(self.monday, 10))
        other = Customer.objects.create(business=self.business, full_name='Wait Er', email='wait@example.com')
        WaitlistEntry.objects.create(business=self.business, customer=other, service=self.service,
                                     desired_date=self.monday)
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel_booking(booking, actor_type=BookingEvent.Actor.OWNER)
        self.assertTrue(Notification.objects.filter(event='waitlist_opening', to_address='wait@example.com').exists())


class NotificationTests(VeloraTestCase):
    def test_confirmation_staff_alert_and_reminders(self):
        booking = self.book(at(self.monday, 10))
        customer_msgs = Notification.objects.filter(booking=booking, recipient_type='customer')
        self.assertEqual(set(customer_msgs.filter(event='booking_confirmed').values_list('channel', flat=True)),
                         {'email', 'sms'})
        self.assertTrue(customer_msgs.filter(event='booking_confirmed', status='sent').exists())
        reminders = customer_msgs.filter(event='reminder', status='queued')
        self.assertEqual(reminders.count(), 4)  # 24h and 2h, on email and SMS
        self.assertIn(str(booking.manage_token), customer_msgs.first().body)
        self.assertTrue(Notification.objects.filter(booking=booking, to_address='amina@example.com').exists())

    def test_opt_out_is_respected(self):
        self.customer.opt_out_sms = True
        self.customer.save()
        booking = self.book(at(self.monday, 10))
        self.assertFalse(Notification.objects.filter(booking=booking, channel='sms').exists())

    def test_cancel_cancels_reminders(self):
        booking = self.book(at(self.monday, 10))
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel_booking(booking, actor_type=BookingEvent.Actor.OWNER)
        self.assertFalse(Notification.objects.filter(booking=booking, event='reminder', status='queued').exists())

    def test_quiet_hours_defer(self):
        self.policy.quiet_hours_start, self.policy.quiet_hours_end = time(21), time(7)
        self.policy.save()
        from notifications.services import apply_quiet_hours
        late = at(self.monday, 22)
        self.assertEqual(apply_quiet_hours(self.business, late), at(self.monday + timedelta(days=1), 7))
        self.assertEqual(apply_quiet_hours(self.business, at(self.monday, 12)), at(self.monday, 12))

    def test_failed_send_is_retried_then_marked_failed(self):
        from notifications import services as notify
        n = notify.queue(business=self.business, event='reminder', channel='email', to_address='x@example.com',
                         body='hi')
        with mock.patch('notifications.providers.ConsoleProvider.send', side_effect=RuntimeError('down')):
            notify.send(n)
            n.refresh_from_db()
            self.assertEqual((n.status, n.attempts), ('queued', 1))
            self.assertIsNotNone(n.next_attempt_at)
            for _ in range(2):
                Notification.objects.filter(pk=n.pk).update(next_attempt_at=None)
                notify.send(n)
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')

    def test_process_command_runs(self):
        call_command('process_notifications', stdout=open('/dev/null', 'w'))


class AnalyticsTests(VeloraTestCase):
    def test_report_and_exports(self):
        b1 = self.book(at(self.monday, 10))
        b2 = self.book(at(self.monday, 12))
        with mock.patch('django.utils.timezone.now', return_value=at(self.monday, 18)):
            services.complete_booking(b1)
            services.mark_no_show(b2)
        services.record_payment(b1, amount=Decimal('1000'), kind=Payment.Kind.BALANCE, method='mobile_money')
        self.login()
        params = {'business': self.business.id, 'date_from': self.monday.isoformat(),
                  'date_to': self.monday.isoformat()}
        report = self.client.get('/bk/analytics/', params).json()
        self.assertEqual(report['bookings']['total'], 2)
        self.assertEqual(report['attendance']['no_show_rate_pct'], 50.0)
        self.assertEqual(report['revenue']['collected'], '1000.00')
        self.assertEqual(report['revenue']['by_payment_method'][0]['method'], 'Mobile money')
        self.assertGreater(report['capacity']['occupancy_pct'], 0)

        csv_response = self.client.get('/bk/analytics/export/', params)
        self.assertEqual(csv_response['Content-Type'], 'text/csv')
        self.assertIn('Jane Doe', csv_response.content.decode())
        summary = self.client.get('/bk/analytics/export/', {**params, 'kind': 'summary'}).content.decode()
        self.assertIn('no-show rate %', summary)


class BookingLinkSlugTests(VeloraTestCase):
    def make_business(self, name):
        n = Business.objects.count()
        owner = User.objects.create_user(f'slug{n}@example.com', 'pass-12345', full_name='Slug Owner')
        return Business.objects.create(
            business_owner=owner, business_name=name, business_description='x',
            whatsapp_number='+254700000009', business_email='x@example.com',
        )

    def test_slug_comes_from_name_and_is_unique(self):
        self.assertEqual(self.business.slug, 'glow-salon')
        self.assertEqual(self.make_business('Glow Salon').slug, 'glow-salon-2')
        self.assertEqual(self.make_business('Glow  Salon!').slug, 'glow-salon-3')
        self.assertEqual(self.make_business('Book').slug, 'book-studio')
        self.assertEqual(self.make_business('123').slug, '123-studio')

    def test_backfill_gives_existing_businesses_unique_slugs(self):
        from importlib import import_module
        from django.apps import apps
        Business.objects.filter(pk=self.other_business.pk).update(slug='')
        self.make_business('Other Spa')  # created after, so it already holds other-spa
        migration = import_module('business.migrations.0007_business_slug')
        migration.backfill_slugs(apps, None)
        slugs = list(Business.objects.values_list('slug', flat=True))
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertIn('glow-salon', slugs)
        self.assertIn('other-spa-2', slugs)

    def test_public_api_works_by_slug_and_by_old_id(self):
        by_slug = self.client.get('/bk/public/glow-salon/')
        self.assertEqual(by_slug.status_code, 200)
        self.assertEqual(by_slug.json()['slug'], 'glow-salon')
        by_id = self.client.get(f'/bk/public/{self.business.id}/')
        self.assertEqual(by_id.json()['slug'], 'glow-salon')
        self.assertEqual(self.client.get('/bk/public/GLOW-SALON/').status_code, 200)
        self.assertEqual(self.client.get('/bk/public/no-such-place/').status_code, 404)
        day = next_weekday(0).isoformat()
        avail = self.client.get('/bk/public/glow-salon/availability/', {'service': self.service.id, 'date': day})
        self.assertEqual(avail.status_code, 200)

    def test_owner_can_edit_slug_with_validation(self):
        self.login()
        url = f'/bn/business/{self.business.id}/'
        ok = self.client.patch(url, {'slug': 'Glow-Westlands'}, format='json')
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()['slug'], 'glow-westlands')
        for bad in ('other-spa', 'dashboard', '12345', 'a', 'has space', '-dash'):
            response = self.client.patch(url, {'slug': bad}, format='json')
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('slug', response.json())
        # Saving other fields keeps the slug.
        self.client.patch(url, {'business_description': 'New'}, format='json')
        self.business.refresh_from_db()
        self.assertEqual(self.business.slug, 'glow-westlands')

    def test_notification_link_uses_slug(self):
        from notifications.services import booking_page_link
        self.assertTrue(booking_page_link(self.business).endswith('/book/glow-salon'))
