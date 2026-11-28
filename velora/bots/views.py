"""Webhooks for Telegram and WhatsApp, and the dashboard's Channels API."""

import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from bookings.models import Booking
from business.mixins import owned_businesses

from . import booking as svc
from . import telegram, whatsapp
from .models import ChatIdentity

logger = logging.getLogger('velora.bots')


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class TelegramWebhookView(View):
    """POST bots/telegram/webhook/: Telegram calls this for every message and button tap."""

    def post(self, request):
        secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '')
        given = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not secret or not hmac.compare_digest(given, secret):
            return HttpResponseForbidden('Bad secret token')
        try:
            update = json.loads(request.body or b'{}')
        except ValueError:
            return HttpResponse(status=400)
        try:
            telegram.process_update(update)
        except Exception:  # noqa: BLE001 - always 200, or Telegram retries the update forever
            logger.exception('Telegram update %s failed', update.get('update_id'))
        return JsonResponse({'ok': True})


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):
    """bots/whatsapp/webhook/: the Callback URL to paste into Meta.

    GET answers Meta's verification with ``hub.challenge``. POST carries
    messages and delivery statuses, signed with the app secret.
    """

    def get(self, request):
        challenge = whatsapp.verify_challenge(request.GET)
        if challenge is None:
            return HttpResponseForbidden('Verification failed')
        return HttpResponse(challenge, content_type='text/plain')

    def post(self, request):
        raw = request.body
        if not whatsapp.valid_signature(raw, request.headers.get('X-Hub-Signature-256', '')):
            return HttpResponseForbidden('Bad signature')
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            return HttpResponse(status=400)
        try:
            whatsapp.process_payload(payload)
        except Exception:  # noqa: BLE001 - always 200, or Meta keeps retrying
            logger.exception('WhatsApp webhook failed')
        return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Dashboard: Channels page
# ---------------------------------------------------------------------------

class ChannelSettingsSerializer(serializers.Serializer):
    telegram_enabled = serializers.BooleanField(required=False)
    whatsapp_enabled = serializers.BooleanField(required=False)
    alert_owner_whatsapp = serializers.BooleanField(required=False)
    unlink_owner_telegram = serializers.BooleanField(required=False)


class ChannelSettingsView(APIView):
    """GET/PATCH bots/settings/?business=<id>: bots on or off, links and numbers."""

    permission_classes = [IsAuthenticated]

    def business(self, request):
        raw = request.query_params.get('business') or request.data.get('business')
        try:
            business_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'business': ['This query parameter is required.']})
        business = owned_businesses(request.user).filter(pk=business_id).first()
        if business is None:
            raise NotFound('Business not found.')
        return business

    def payload(self, business):
        bot = svc.settings_for(business)
        since = timezone.now() - timedelta(days=30)
        stats = Booking.objects.filter(business=business, channel__in=['telegram', 'whatsapp']).aggregate(
            telegram_total=Count('id', filter=Q(channel='telegram')),
            whatsapp_total=Count('id', filter=Q(channel='whatsapp')),
            telegram_30d=Count('id', filter=Q(channel='telegram', created_at__gte=since)),
            whatsapp_30d=Count('id', filter=Q(channel='whatsapp', created_at__gte=since)),
        )
        account_ids = business.customers.filter(account__isnull=False).values('account_id')
        chats = dict(ChatIdentity.objects.filter(account_id__in=account_ids)
                     .values_list('channel').annotate(n=Count('account_id', distinct=True)))
        return {
            'business': business.id,
            'booking_link': svc.booking_link(business),
            'telegram': {
                'enabled': bot.telegram_enabled,
                'configured': telegram.configured() and bool(settings.TELEGRAM_BOT_USERNAME),
                'bot_username': settings.TELEGRAM_BOT_USERNAME,
                'link': svc.telegram_link(business),
                'customers': chats.get('telegram', 0),
                'bookings_total': stats['telegram_total'],
                'bookings_30d': stats['telegram_30d'],
                'owner_linked': bool(bot.owner_telegram_chat_id),
                'owner_name': bot.owner_telegram_name,
                'owner_link': svc.owner_telegram_link(bot) if not bot.owner_telegram_chat_id else '',
            },
            'whatsapp': {
                'enabled': bot.whatsapp_enabled,
                'configured': whatsapp.configured() and bool(settings.WHATSAPP_DISPLAY_NUMBER),
                'number': settings.WHATSAPP_DISPLAY_NUMBER,
                'link': svc.whatsapp_link(business),
                'customers': chats.get('whatsapp', 0),
                'bookings_total': stats['whatsapp_total'],
                'bookings_30d': stats['whatsapp_30d'],
                'alert_owner': bot.alert_owner_whatsapp,
                'owner_number': business.whatsapp_number,
            },
        }

    def get(self, request):
        return Response(self.payload(self.business(request)))

    def patch(self, request):
        business = self.business(request)
        s = ChannelSettingsSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        bot = svc.settings_for(business)
        data = s.validated_data
        for field in ('telegram_enabled', 'whatsapp_enabled', 'alert_owner_whatsapp'):
            if field in data:
                setattr(bot, field, data[field])
        if data.get('unlink_owner_telegram'):
            bot.owner_telegram_chat_id = ''
            bot.owner_telegram_name = ''
            bot.owner_link_token = ''
        bot.save()
        return Response(self.payload(business))
