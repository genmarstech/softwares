import hashlib
import hmac
import json
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from bookings import services as booking_services
from bookings.models import Booking, Customer
from business.models import Business, BusinessStaff, Service
from customers.models import CustomerAccount
from notifications.models import Notification

from . import engine, providers, telegram, whatsapp
from .models import BotSettings, ChatIdentity

User = get_user_model()
NAIROBI = ZoneInfo('Africa/Nairobi')

BOT_SETTINGS = dict(
    TELEGRAM_BOT_TOKEN='123:abc', TELEGRAM_BOT_USERNAME='VeloraBookingsBot', TELEGRAM_WEBHOOK_SECRET='tg-secret',
    WHATSAPP_TOKEN='wa-token', WHATSAPP_PHONE_NUMBER_ID='999', WHATSAPP_VERIFY_TOKEN='verify-me',
    WHATSAPP_APP_SECRET='app-secret', WHATSAPP_DISPLAY_NUMBER='+254700111222',
    VELORA_NOTIFICATION_PROVIDERS={'email': 'console', 'sms': 'console', 'whatsapp': 'whatsapp_cloud',
                                   'telegram': 'telegram', 'push': 'console'},
)


def next_monday():
    today = timezone.now().astimezone(NAIROBI).date()
    return today + timedelta(days=(7 - today.weekday()) or 7)


class FakeAPI:
    """Stands in for Telegram and Meta: records every call and answers ok."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, payload, headers=None, timeout=15):
        self.calls.append((url, payload))
        if 'graph.facebook.com' in url:
            return {'messages': [{'id': f'wamid.{len(self.calls)}'}]}
        return {'ok': True, 'result': {'message_id': len(self.calls)}}

    def telegram(self, method='sendMessage'):
        return [p for u, p in self.calls if u.endswith(f'/{method}')]

    def whatsapp(self):
        return [p for u, p in self.calls if 'graph.facebook.com' in u]

    def texts(self, chat_id=None):
        return [p['text'] for p in self.telegram() if chat_id is None or str(p['chat_id']) == str(chat_id)]

    def buttons(self, payload):
        markup = payload.get('reply_markup') or {}
        return [b.get('callback_data') for row in markup.get('inline_keyboard', []) for b in row]


@override_settings(**BOT_SETTINGS)
class BotTestCase(TransactionTestCase):
    def setUp(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.api = FakeAPI()
        patcher = mock.patch('bots.http.post_json', self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.owner = User.objects.create_user('owner@example.com', 'pass-12345', full_name='Owner One')
        self.business = Business.objects.create(
            business_owner=self.owner, business_name='Glow Salon', business_description='Hair',
            whatsapp_number='+254700000000', business_email='glow@example.com',
        )
        booking_services.ensure_business_defaults(self.business)
        # The owner has confirmed their opening hours, so the business takes online bookings.
        booking_services.confirm_opening_hours(self.business)
        policy = booking_services.get_policy(self.business)
        policy.min_notice_minutes = 0
        policy.notification_channels = ['email']
        policy.save()
        self.service = Service.objects.create(
            business=self.business, service_name='Haircut', price=Decimal('1000'), duration_minutes=60,
        )
        self.amina = BusinessStaff.objects.create(
            business=self.business, staff_name='Amina', staff_email='amina@example.com', id_number='1234',
        )
        self.bot = BotSettings.objects.create(business=self.business, telegram_enabled=True, whatsapp_enabled=True)
        self.monday = next_monday()

    # -- Telegram helpers ---------------------------------------------------

    update_id = 1000

    def tg(self, chat_id=501, text='', data='', contact=None, user_id=None):
        BotTestCase.update_id += 1
        user = {'id': user_id or chat_id, 'first_name': 'Jane', 'last_name': 'Doe'}
        if data:
            update = {'update_id': self.update_id, 'callback_query': {
                'id': f'cb{self.update_id}', 'from': user, 'data': data,
                'message': {'chat': {'id': chat_id, 'type': 'private'}}}}
        else:
            message = {'chat': {'id': chat_id, 'type': 'private'}, 'from': user, 'text': text}
            if contact:
                message['contact'] = contact
            update = {'update_id': self.update_id, 'message': message}
        return telegram.process_update(update)

    def tg_join(self, chat_id=501, phone='254711111111'):
        self.tg(chat_id, text='/start glow-salon')
        return self.tg(chat_id, contact={'phone_number': phone, 'user_id': chat_id, 'first_name': 'Jane'})

    # -- WhatsApp helpers --------------------------------------------------

    def wa_post(self, messages=(), statuses=(), sign=True, client=None):
        body = json.dumps({'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': {
            'messaging_product': 'whatsapp', 'metadata': {'phone_number_id': '999'},
            'contacts': [{'wa_id': '254722222222', 'profile': {'name': 'Wanjiru Kamau'}}],
            'messages': list(messages), 'statuses': list(statuses)}}]}]}).encode()
        signature = 'sha256=' + hmac.new(b'app-secret', body, hashlib.sha256).hexdigest() if sign else 'sha256=bad'
        return (client or APIClient()).generic('POST', '/bots/whatsapp/webhook/', body,
                                               content_type='application/json', HTTP_X_HUB_SIGNATURE_256=signature)

    wa_counter = 0

    def wa_text(self, text, sender='254722222222'):
        BotTestCase.wa_counter += 1
        return {'from': sender, 'id': f'wamid.in{self.wa_counter}', 'type': 'text', 'text': {'body': text}}

    def wa_tap(self, reply_id, sender='254722222222', kind='list_reply'):
        BotTestCase.wa_counter += 1
        return {'from': sender, 'id': f'wamid.in{self.wa_counter}', 'type': 'interactive',
                'interactive': {'type': kind, kind: {'id': reply_id, 'title': 'x'}}}


class TelegramFlowTests(BotTestCase):
    def test_deep_link_asks_for_phone_then_shows_menu(self):
        replies = self.tg(text='/start glow-salon')
        self.assertTrue(replies[0].request_contact)
        self.assertIn('Glow Salon', replies[0].text)
        self.assertEqual(self.api.telegram()[-1]['reply_markup']['keyboard'][0][0]['request_contact'], True)

        replies = self.tg_join()
        account = CustomerAccount.objects.get(phone_number='+254711111111')
        self.assertTrue(account.phone_verified)
        self.assertEqual(account.full_name, 'Jane Doe')
        customer = Customer.objects.get(business=self.business, account=account)
        self.assertIsNotNone(customer.joined_at)
        self.assertEqual(customer.first_channel, 'telegram')
        self.assertIn('welcome to Glow Salon', replies[-1].text)
        self.assertEqual([o.id for o in replies[-1].options], ['new'])

    def test_someone_elses_contact_is_refused(self):
        self.tg(text='/start glow-salon')
        replies = self.tg(contact={'phone_number': '254799999999', 'user_id': 777})
        self.assertTrue(replies[0].request_contact)
        self.assertFalse(CustomerAccount.objects.exists())

    def test_book_with_buttons_then_owner_confirms_from_telegram(self):
        self.bot.owner_telegram_chat_id = '900'
        self.bot.save()
        BusinessStaff.objects.create(business=self.business, staff_name='Brian', staff_email='b@example.com',
                                     id_number='5678')
        self.tg_join()
        replies = self.tg(data='new')
        self.assertIn(f'svc:{self.service.id}', [o.id for o in replies[0].options])
        replies = self.tg(data=f'svc:{self.service.id}')
        self.assertEqual(replies[0].options[0].id, 'stf:0')
        replies = self.tg(data=f'stf:{self.amina.id}')
        self.assertIn(f'day:{self.monday:%Y%m%d}', [o.id for o in replies[0].options])
        replies = self.tg(data=f'day:{self.monday:%Y%m%d}')
        self.assertIn(f'slot:{self.monday:%Y%m%d}1000', [o.id for o in replies[0].options])
        # Times are laid out three to a row.
        self.assertEqual(len(self.api.telegram()[-1]['reply_markup']['inline_keyboard'][0]), 3)
        replies = self.tg(data=f'slot:{self.monday:%Y%m%d}1000')
        self.assertIn('Please confirm', replies[0].text)
        replies = self.tg(data='ok')
        self.assertIn('Request sent', replies[0].text)

        booking = Booking.objects.get()
        self.assertEqual(booking.channel, 'telegram')
        self.assertEqual(booking.status, 'pending')
        self.assertEqual(booking.staff, self.amina)
        self.assertEqual(booking.start, datetime.combine(self.monday, time(10), tzinfo=NAIROBI))
        # The customer's chat got the bot's answer, not a second "booking received" notification.
        self.assertFalse(Notification.objects.filter(channel='telegram', recipient_type='customer').exists())
        alert = Notification.objects.get(channel='telegram', recipient_type='business')
        self.assertEqual(alert.to_address, '900')
        self.assertEqual(alert.status, 'sent')
        owner_msg = [p for p in self.api.telegram() if str(p['chat_id']) == '900'][-1]
        self.assertEqual(self.api.buttons(owner_msg), [f'oc:{booking.id}', f'od:{booking.id}'])

        # A customer cannot confirm their own booking.
        replies = self.tg(data=f'oc:{booking.id}')
        self.assertIn('Only the business', replies[0].text)
        replies = self.tg(chat_id=900, data=f'oc:{booking.id}')
        self.assertIn('Confirmed', replies[0].text)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        confirmed = Notification.objects.get(channel='telegram', event='booking_confirmed')
        self.assertEqual(confirmed.to_address, '501')
        reminders = Notification.objects.filter(channel='telegram', event='reminder', status='queued')
        self.assertEqual(reminders.count(), 2)  # 24h and 2h before

    def test_book_again_reschedule_and_cancel(self):
        self.tg_join()
        customer = Customer.objects.get(account__phone_number='+254711111111')
        past = timezone.now() - timedelta(days=10)
        Booking.objects.create(business=self.business, customer=customer, service=self.service, staff=self.amina,
                               start=past, end=past + timedelta(hours=1), block_start=past,
                               block_end=past + timedelta(hours=1), status='completed', price=1000)
        replies = self.tg(text='menu')
        self.assertEqual(replies[0].options[0].id, 'again')
        replies = self.tg(data='again')
        self.assertIn('Haircut with Amina', replies[0].text)
        self.tg(data=f'slot:{self.monday:%Y%m%d}1100')
        self.tg(data='ok')
        booking = Booking.objects.get(status='pending')
        self.assertEqual((booking.service, booking.staff), (self.service, self.amina))

        replies = self.tg(data='mine')
        self.assertEqual(replies[0].options[0].id, f'bk:{booking.id}')
        self.tg(data=f'rs:{booking.id}')
        self.tg(data=f'slot:{self.monday:%Y%m%d}1400')
        replies = self.tg(data='ok')
        self.assertIn('14:00', replies[0].text)
        booking.refresh_from_db()
        self.assertEqual(booking.start.astimezone(NAIROBI).hour, 14)

        self.tg(data=f'cx:{booking.id}')
        replies = self.tg(data=f'cxy:{booking.id}')
        self.assertIn('Cancelled', replies[0].text)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')

    def test_cannot_touch_another_customers_booking(self):
        self.tg_join()
        other = Customer.objects.create(business=self.business, full_name='Other', phone_number='+254733333333')
        start = datetime.combine(self.monday, time(12), tzinfo=NAIROBI)
        booking = Booking.objects.create(business=self.business, customer=other, service=self.service,
                                         staff=self.amina, start=start, end=start + timedelta(hours=1),
                                         block_start=start, block_end=start + timedelta(hours=1), price=1000)
        replies = self.tg(data=f'cxy:{booking.id}')
        self.assertIn('not found', replies[0].text)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'pending')

    def test_disabled_business_points_to_the_web_link(self):
        self.bot.telegram_enabled = False
        self.bot.save()
        replies = self.tg_join()
        self.assertIn('does not take bookings here', replies[-1].text)
        self.assertEqual(replies[-1].link[1], 'http://localhost:5173/book/glow-salon')

    def test_stop_turns_off_chat_updates(self):
        self.tg_join()
        self.tg(text='stop')
        customer = Customer.objects.get(account__phone_number='+254711111111')
        from notifications.services import customer_targets
        self.assertNotIn('telegram', dict(customer_targets(self.business, customer)))
        self.tg(text='start')
        self.assertIn('telegram', dict(customer_targets(self.business, customer)))

    def test_verified_phone_links_guest_history_even_with_an_email(self):
        guest = Customer.objects.create(business=self.business, full_name='Jane', email='jane@example.com',
                                        phone_number='0711111111')
        self.tg_join()
        guest.refresh_from_db()
        self.assertEqual(guest.account.phone_number, '+254711111111')

    def test_owner_links_telegram_with_single_use_link(self):
        token = self.bot.new_owner_link_token()
        replies = self.tg(chat_id=900, text=f'/start o_{token}')
        self.assertIn('New bookings for Glow Salon', replies[0].text)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.owner_telegram_chat_id, '900')
        replies = self.tg(chat_id=901, text=f'/start o_{token}')
        self.assertIn('expired', replies[0].text)

    def test_webhook_needs_secret_and_ignores_repeats(self):
        client = APIClient()
        update = {'update_id': 77, 'message': {'chat': {'id': 5, 'type': 'private'}, 'from': {'id': 5}, 'text': 'hi'}}
        bad = client.post('/bots/telegram/webhook/', update, format='json')
        self.assertEqual(bad.status_code, 403)
        for _ in range(2):
            ok = client.post('/bots/telegram/webhook/', update, format='json',
                             HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='tg-secret')
            self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(self.api.telegram()), 1)


class WhatsAppTests(BotTestCase):
    def test_verify_challenge(self):
        client = APIClient()
        ok = client.get('/bots/whatsapp/webhook/', {'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me',
                                                    'hub.challenge': '12345'})
        self.assertEqual((ok.status_code, ok.content), (200, b'12345'))
        bad = client.get('/bots/whatsapp/webhook/', {'hub.mode': 'subscribe', 'hub.verify_token': 'nope',
                                                     'hub.challenge': '12345'})
        self.assertEqual(bad.status_code, 403)

    def test_signature_is_required(self):
        self.assertEqual(self.wa_post([self.wa_text('hi')], sign=False).status_code, 403)
        self.assertEqual(self.api.whatsapp(), [])

    def test_prefilled_link_message_books_with_lists(self):
        text = 'Hi Glow Salon! I would like to book. (ref: glow-salon)'
        self.assertEqual(self.wa_post([self.wa_text(text)]).status_code, 200)
        account = CustomerAccount.objects.get(phone_number='+254722222222')
        self.assertTrue(account.phone_verified)
        self.assertEqual(account.full_name, 'Wanjiru Kamau')
        menu = self.api.whatsapp()[-1]
        self.assertEqual(menu['to'], '254722222222')
        self.assertEqual(menu['interactive']['type'], 'button')
        self.assertEqual(menu['interactive']['action']['buttons'][0]['reply']['id'], 'new')

        self.wa_post([self.wa_tap('new', kind='button_reply')])
        services_msg = self.api.whatsapp()[-1]
        self.assertEqual(services_msg['interactive']['type'], 'list')
        rows = services_msg['interactive']['action']['sections'][0]['rows']
        self.assertEqual(rows[0]['title'], 'Haircut')
        self.assertEqual(rows[0]['description'], 'KES 1,000 · 60 min')
        self.wa_post([self.wa_tap(f'svc:{self.service.id}')])
        self.wa_post([self.wa_tap(f'day:{self.monday:%Y%m%d}')])
        self.wa_post([self.wa_tap(f'slot:{self.monday:%Y%m%d}0900')])
        self.wa_post([self.wa_tap('ok', kind='button_reply')])
        booking = Booking.objects.get()
        self.assertEqual(booking.channel, 'whatsapp')
        self.assertIn('Request sent', self.api.whatsapp()[-1]['interactive']['body']['text'])

    def test_repeated_webhook_is_handled_once(self):
        message = self.wa_text('ref: glow-salon')
        self.wa_post([message])
        self.wa_post([message])
        self.assertEqual(len(self.api.whatsapp()), 1)

    def test_reminder_uses_a_template_outside_the_24_hour_window(self):
        self.wa_post([self.wa_text('ref: glow-salon')])
        identity = ChatIdentity.objects.get(channel='whatsapp')
        customer = Customer.objects.get(account=identity.account)
        start = datetime.combine(self.monday, time(10), tzinfo=NAIROBI)
        booking = booking_services.create_booking(business=self.business, service=self.service, customer=customer,
                                                  start=start, staff=self.amina, status='confirmed')
        reminder = Notification.objects.filter(channel='whatsapp', event='reminder').first()
        self.assertEqual(reminder.to_address, '254722222222')

        # Within 24 hours of their last message: a normal message with a button.
        sent = providers.WhatsAppCloudProvider().send(reminder)
        self.assertTrue(sent.startswith('wamid.'))
        self.assertEqual(self.api.whatsapp()[-1]['type'], 'interactive')

        ChatIdentity.objects.filter(pk=identity.pk).update(last_inbound_at=timezone.now() - timedelta(days=2))
        providers.WhatsAppCloudProvider().send(reminder)
        payload = self.api.whatsapp()[-1]
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'velora_appointment_reminder')
        params = [p['text'] for p in payload['template']['components'][0]['parameters']]
        self.assertEqual(params[:3], ['Wanjiru', 'Glow Salon', 'Haircut with Amina'])
        self.assertEqual(payload['template']['components'][1]['parameters'][0]['payload'], 'mine:glow-salon')
        self.assertEqual(booking.status, 'confirmed')

    def test_template_quick_reply_continues_the_chat(self):
        self.wa_post([self.wa_text('ref: glow-salon')])
        message = {'from': '254722222222', 'id': 'wamid.quick', 'type': 'button',
                   'button': {'payload': 'mine:glow-salon', 'text': 'My bookings'}}
        self.wa_post([message])
        self.assertIn('no upcoming bookings', self.api.whatsapp()[-1]['interactive']['body']['text'])

    def test_delivery_status_updates_the_notification_log(self):
        n = Notification.objects.create(business=self.business, event='reminder', channel='whatsapp',
                                        to_address='254722222222', body='x', status='sent',
                                        provider_message_id='wamid.out1')
        self.wa_post(statuses=[{'id': 'wamid.out1', 'status': 'delivered'}])
        n.refresh_from_db()
        self.assertEqual(n.status, 'delivered')
        self.wa_post(statuses=[{'id': 'wamid.out1', 'status': 'failed',
                                'errors': [{'code': 131047, 'title': 'Re-engagement message'}]}])
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')
        self.assertIn('131047', n.error)

    def test_owner_confirms_from_the_business_whatsapp_number(self):
        self.bot.alert_owner_whatsapp = True
        self.bot.save()
        customer = Customer.objects.create(business=self.business, full_name='Jane', phone_number='+254711111111')
        start = datetime.combine(self.monday, time(10), tzinfo=NAIROBI)
        booking = booking_services.create_booking(business=self.business, service=self.service, customer=customer,
                                                  start=start, staff=self.amina)
        alert = Notification.objects.get(channel='whatsapp', recipient_type='business')
        self.assertEqual(alert.to_address, '254700000000')
        template = self.api.whatsapp()[-1]['template']
        self.assertEqual(template['name'], 'velora_new_booking')
        self.assertEqual([c['parameters'][0]['payload'] for c in template['components'][1:]],
                         [f'oc:{booking.id}', f'od:{booking.id}'])
        # The same button from any other number is refused.
        self.wa_post([{'from': '254722222222', 'id': 'wamid.x1', 'type': 'button',
                       'button': {'payload': f'od:{booking.id}'}}])
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'pending')
        self.wa_post([{'from': '254700000000', 'id': 'wamid.x2', 'type': 'button',
                       'button': {'payload': f'od:{booking.id}'}}])
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')


class ChannelSettingsApiTests(BotTestCase):
    def test_owner_sees_links_and_toggles_bots(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        data = client.get('/bots/settings/', {'business': self.business.id}).json()
        self.assertEqual(data['telegram']['link'], 'https://t.me/VeloraBookingsBot?start=glow-salon')
        self.assertTrue(data['whatsapp']['link'].startswith('https://wa.me/254700111222?text='))
        self.assertIn('ref%3A%20glow-salon', data['whatsapp']['link'])
        self.assertTrue(data['telegram']['owner_link'].startswith('https://t.me/VeloraBookingsBot?start=o_'))
        self.assertTrue(data['telegram']['configured'])

        data = client.patch(f'/bots/settings/?business={self.business.id}', {'telegram_enabled': False},
                            format='json').json()
        self.assertFalse(data['telegram']['enabled'])
        self.assertTrue(data['whatsapp']['enabled'])

    def test_other_owners_cannot_see_settings(self):
        other = User.objects.create_user('x@example.com', 'pass-12345', full_name='X')
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(client.get('/bots/settings/', {'business': self.business.id}).status_code, 404)
        self.assertIn(APIClient().get('/bots/settings/', {'business': self.business.id}).status_code, (401, 403))


class RenderingTests(BotTestCase):
    def test_long_titles_are_cut_for_whatsapp_lists(self):
        reply = engine.Reply('Pick', options=[engine.Option(f'svc:{i}', 'Knotless braids, waist length extra')
                                              for i in range(5)])
        payload = whatsapp.message_payload('254700', reply)
        row = payload['interactive']['action']['sections'][0]['rows'][0]
        self.assertLessEqual(len(row['title']), 24)

    def test_template_params_have_no_new_lines(self):
        self.assertEqual(providers._param('a\nb\t  c'), 'a b c')
