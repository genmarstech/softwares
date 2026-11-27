from datetime import timedelta

from django.test import override_settings
from rest_framework.test import APIClient

from bookings.models import Booking, Customer
from bookings.tests import VeloraTestCase, at
from business.models import Business

from .models import CustomerAccount
from .phones import normalize_phone


class PhoneTests(VeloraTestCase):
    def test_local_and_international_forms_match(self):
        for raw in ['0712 345 678', '+254 712 345 678', '254712345678', '712345678', '00254712345678']:
            self.assertEqual(normalize_phone(raw), '+254712345678', raw)
        self.assertEqual(normalize_phone('+44 7700 900123'), '+447700900123')
        self.assertEqual(normalize_phone('hello'), '')
        self.assertEqual(normalize_phone('123'), '')


class CustomerAccountTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.customer_client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.customer_client.get('/cu/session/').json()['csrfToken']

    def post(self, url, data, client=None):
        client = client or self.customer_client
        return client.post(url, data, format='json', HTTP_X_CSRFTOKEN=self.csrf)

    def register(self, **overrides):
        data = {'business': 'glow-salon', 'full_name': 'Mary Wanjiku', 'phone_number': '0722 000 111',
                'email': 'mary@example.com', 'password': 'Velora-pass-1'}
        data.update(overrides)
        return self.post('/cu/register/', data)

    def test_register_on_link_joins_the_business(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body['authenticated'])
        self.assertTrue(body['joined'])
        self.assertEqual(body['account']['phone_number'], '+254722000111')
        account = CustomerAccount.objects.get()
        customer = Customer.objects.get(account=account)
        self.assertEqual(customer.business, self.business)
        self.assertIsNotNone(customer.joined_at)
        self.assertNotEqual(account.password, 'Velora-pass-1')
        session = self.customer_client.get('/cu/session/', {'business': 'glow-salon'}).json()
        self.assertTrue(session['authenticated'])
        self.assertTrue(session['joined'])

    def test_writes_need_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get('/cu/session/')
        response = client.post('/cu/register/', {'business': 'glow-salon', 'full_name': 'Mary',
                                                 'phone_number': '0722000111', 'password': 'Velora-pass-1'},
                               format='json')
        self.assertEqual(response.status_code, 403)

    def test_one_account_per_phone_number(self):
        self.register()
        response = self.register(phone_number='+254722000111', email='other@example.com')
        self.assertEqual(response.status_code, 400)
        self.assertIn('phone_number', response.json())

    def test_login_with_any_phone_format_and_join_another_business(self):
        self.register()
        self.post('/cu/logout/', {})
        self.assertFalse(self.customer_client.get('/cu/session/').json()['authenticated'])
        bad = self.post('/cu/login/', {'business': 'other-spa', 'phone_number': '0722000111', 'password': 'nope'})
        self.assertEqual(bad.status_code, 400)
        ok = self.post('/cu/login/', {'business': 'other-spa', 'phone_number': '+254 722 000 111',
                                      'password': 'Velora-pass-1'})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertTrue(ok.json()['joined'])
        self.assertEqual(Customer.objects.filter(account__isnull=False).count(), 2)

    def test_signed_in_booking_needs_no_details_and_waits_for_confirmation(self):
        self.register()
        response = self.post('/bk/public/glow-salon/bookings/', {
            'service': self.service.id, 'staff': self.staff.id, 'start': at(self.monday, 10).isoformat(),
        })
        self.assertEqual(response.status_code, 201, response.content)
        booking = Booking.objects.get()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertEqual(booking.customer.account.phone_number, '+254722000111')
        self.assertEqual(booking.customer.email, 'mary@example.com')

        history = self.customer_client.get('/cu/bookings/', {'business': 'glow-salon'}).json()
        self.assertEqual(len(history['upcoming']), 1)
        self.assertEqual(history['upcoming'][0]['service_id'], self.service.id)
        self.assertEqual(history['favourites'][0]['service'], self.service.id)
        self.assertEqual(history['favourites'][0]['staff'], self.staff.id)

    def test_past_visit_offers_rebook(self):
        self.register()
        customer = Customer.objects.get(account__isnull=False)
        last_week = self.monday - timedelta(days=14)
        Booking.objects.create(
            business=self.business, customer=customer, service=self.service, staff=self.staff,
            start=at(last_week, 10), end=at(last_week, 11), block_start=at(last_week, 10),
            block_end=at(last_week, 11), status=Booking.Status.COMPLETED,
        )
        history = self.customer_client.get('/cu/bookings/', {'business': 'glow-salon'}).json()
        self.assertEqual(history['upcoming'], [])
        self.assertEqual(history['past'][0]['rebook'],
                         {'available': True, 'service': self.service.id, 'staff': self.staff.id})

    def test_guest_history_is_linked_only_when_email_matches(self):
        # self.customer (Jane, +254711111111, jane@example.com) booked as a guest before.
        self.book(at(self.monday, 10))
        self.register(full_name='Not Jane', phone_number='0711111111', email='someone@example.com')
        self.assertIsNone(Customer.objects.get(pk=self.customer.pk).account)
        history = self.customer_client.get('/cu/bookings/', {'business': 'glow-salon'}).json()
        self.assertEqual(history['upcoming'], [])


    def test_guest_record_with_same_phone_and_email_is_linked(self):
        self.book(at(self.monday, 10))
        self.register(full_name='Jane Doe', phone_number='0711111111', email='jane@example.com')
        self.assertIsNotNone(Customer.objects.get(pk=self.customer.pk).account)
        history = self.customer_client.get('/cu/bookings/', {'business': 'glow-salon'}).json()
        self.assertEqual(len(history['upcoming']), 1)

    def test_bookings_need_sign_in(self):
        response = APIClient().get('/cu/bookings/', {'business': 'glow-salon'})
        self.assertEqual(response.status_code, 401)

    def test_owner_login_is_separate_from_customer_login(self):
        self.register()
        # The customer session does not make them a business owner.
        response = self.customer_client.get('/bn/business/')
        self.assertIn(response.status_code, (401, 403))

    def test_setup_step_counts_customers_reached_through_the_link(self):
        self.client.force_authenticate(self.owner)

        def share_step():
            steps = self.client.get('/bk/overview/', {'business': self.business.id}).json()['setup']['steps']
            return next(s for s in steps if s['key'] == 'share')

        self.assertEqual(share_step()['progress']['value'], 0)
        self.register()
        # A guest who booked online also counts; one added by staff does not.
        guest = Customer.objects.create(business=self.business, full_name='Guest', phone_number='+254733000000')
        self.book(at(self.monday, 11), customer=guest, channel=Booking.Channel.ONLINE)
        self.book(at(self.monday, 12), channel=Booking.Channel.STAFF)
        step = share_step()
        self.assertEqual(step['progress'], {'value': 2, 'target': 5, 'reached': 2})
        self.assertFalse(step['done'])
        for i in range(3):
            Customer.objects.create(business=self.business, full_name=f'C{i}', joined_at=at(self.monday, 9))
        self.assertTrue(share_step()['done'])

    def test_other_business_link_missing_returns_404(self):
        response = self.register(business='does-not-exist')
        self.assertEqual(response.status_code, 404)

    @override_settings(VELORA_DEFAULT_COUNTRY_CODE='255')
    def test_default_country_code_is_configurable(self):
        self.assertEqual(normalize_phone('0712345678'), '+255712345678')


class DefaultPolicyTests(VeloraTestCase):
    def test_online_bookings_wait_for_the_team_by_default(self):
        fresh = Business.objects.create(
            business_owner=self.owner, business_name='Fresh Nails', business_description='Nails',
            whatsapp_number='+254700000009', business_email='fresh@example.com',
        )
        from bookings import services
        self.assertFalse(services.get_policy(fresh).auto_confirm)
