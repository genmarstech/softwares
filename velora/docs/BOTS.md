# Telegram and WhatsApp bots

Customers book, rebook, reschedule and cancel by chatting with one Velora bot on Telegram and one
Velora number on WhatsApp. Every business shares them; the link a business shares tells the bot which
business the customer wants:

| Channel | Customer link | How the bot knows who it is |
| --- | --- | --- |
| Telegram | `https://t.me/<bot username>?start=<business-slug>` | The customer taps **Share my phone number**. Telegram only sends the person's own, verified number. |
| WhatsApp | `https://wa.me/<velora number>?text=Hi Glow Salon! I would like to book. (ref: glow-salon)` | The number they message from. |

Either way the bot finds or creates the customer's Velora account (`customers.CustomerAccount`) by that
phone number, marks the number verified, and joins them to the business, which counts towards the
"Share your link with 5 customers" setup step. Businesses turn each bot on, copy their links and
link their own Telegram for alerts on the dashboard's **Channels** page.

What the bot does:

- **Menu**: Book again (their last service and stylist), New booking, My bookings.
- **Booking**: service, team member (or anyone), day, time, confirm. Bookings follow the business's
  rules like the web page, so they arrive as **pending** for the team to confirm unless auto-confirm is on.
- **My bookings**: reschedule or cancel within the business's windows.
- **Updates** (same events as email, through `process_notifications`): booking received / confirmed /
  moved / cancelled, reminders 24 h and 2 h before (the policy's `reminder_hours`), and the
  "Thanks for visiting, book again?" follow-up after a visit is marked complete, each with a button that
  continues the chat.
- **Owner alerts**: new bookings go to the owner's linked Telegram (and, if turned on, the business's
  WhatsApp number) with **Confirm** and **Decline** buttons. Only that chat can use them.
- `STOP` turns chat updates off, `START` turns them back on.

Code: `bots/booking.py` (the shared booking service), `bots/engine.py` (the conversation),
`bots/telegram.py` and `bots/whatsapp.py` (thin adapters), `bots/providers.py` (notification delivery).

## Telegram setup

1. In Telegram, message **@BotFather**, send `/newbot`, pick a name (`Velora Bookings`) and a username
   ending in `bot` (`VeloraBookingsBot`). Copy the token it gives you.
2. Optional, in BotFather: `/setdescription`, `/setabouttext` and `/setuserpic` with the Velora logo.
3. In the backend `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   TELEGRAM_BOT_USERNAME=VeloraBookingsBot
   TELEGRAM_WEBHOOK_SECRET=<any long random string>
   ```
4. Start receiving messages, either:
   - **Deployed** (public HTTPS): `python manage.py telegram_webhook --url https://<api-host>`.
     Telegram will call `https://<api-host>/bots/telegram/webhook/`.
   - **On your laptop**: `python manage.py telegram_poll` (no public URL needed; it removes the
     webhook, so run `telegram_webhook` again after you deploy).
5. On the dashboard, open **Channels**, turn Telegram on and open the link. Tap **Link my Telegram**
   to get new-booking alerts.

## WhatsApp setup (Meta Cloud API)

You already have the Meta app **Velora Bookings**. In it, open **WhatsApp > API setup**:

1. Copy the **Phone number ID** and a token. The temporary token expires after 24 hours; for anything
   longer create a **System user** in Business settings, give it the app with `whatsapp_business_messaging`
   and `whatsapp_business_management`, and generate a permanent token.
2. **App settings > Basic**: copy the **App secret**.
3. In the backend `.env`:
   ```
   WHATSAPP_TOKEN=<token>
   WHATSAPP_PHONE_NUMBER_ID=<phone number id>
   WHATSAPP_VERIFY_TOKEN=<any string you choose>
   WHATSAPP_APP_SECRET=<app secret>
   WHATSAPP_DISPLAY_NUMBER=<the number customers message, e.g. +15551234567 for the test number>
   ```
   Restart the backend.
4. **WhatsApp > Configuration > Webhook** (the "Configure Webhooks" step of Production setup):
   - **Callback URL**: `https://<api-host>/bots/whatsapp/webhook/`
     (local: run `ngrok http 8000` and use `https://<id>.ngrok-free.app/bots/whatsapp/webhook/`,
     and add that host to `ALLOWED_HOSTS`).
   - **Verify token**: the same `WHATSAPP_VERIFY_TOKEN`.
   - Click **Verify and save**, then under **Webhook fields** subscribe to **messages**.
5. With the test number, only the (up to 5) phone numbers added under **API setup > To** can chat with
   the bot. Add yours, then message the test number `ref: <your business slug>`.
6. On the dashboard **Channels** page, turn WhatsApp on.

To go live:

- Finish **Business verification** (pending for Genmars Tech) and add a real phone number
  (**Register your WhatsApp phone number**), then update `WHATSAPP_PHONE_NUMBER_ID` and
  `WHATSAPP_DISPLAY_NUMBER`.
- **Publish the app** (needs a privacy policy URL). While the app is unpublished, Meta only sends
  test webhooks from the dashboard, not real messages.
- **Add a payment method** for business-initiated (template) messages.

### Message templates

Inside 24 hours of a customer's last message the bot sends normal messages with buttons. Reminders
and alerts outside that window must use **approved templates**. Create these under
**WhatsApp Manager > Message templates**, category **Utility**, language **English (en)**, with exactly
these names, variables and quick-reply buttons (the names can be changed with `WHATSAPP_TEMPLATES`
in settings):

| Name | Body | Quick-reply buttons |
| --- | --- | --- |
| `velora_appointment_reminder` | Hi {{1}}, this is a reminder from {{2}}: your {{3}} appointment is on {{4}}. Tap below to see or change your booking. | My bookings |
| `velora_booking_confirmed` | Hi {{1}}, your {{2}} booking at {{3}} on {{4}} is confirmed. See you then! | none |
| `velora_book_again` | Hi {{1}}, thank you for visiting {{2}}. Ready for your next {{3}}? Tap below to pick a time. | Book again |
| `velora_new_booking` | New booking request at {{1}}: {{2}} booked {{3}} on {{4}}. Confirm or decline below. | Confirm, Decline (in that order) |
| `velora_booking_update` | Hi {{1}}, an update from {{2}}: {{3}} Reply here if you have any questions. | none |

Sample values for Meta's review: `Jane`, `Glow Salon`, `Haircut with Amina`, `Mon 01 Dec 2026 at 10:00`.
Until they are approved, messages outside the 24-hour window fail and show as failed in the
dashboard's notification log; everything inside the window works without them.

## Running and testing

- Reminders and retries go out with the existing `python manage.py process_notifications` (every minute).
- Tests mock Telegram and Meta: `USE_SQLITE=1 python manage.py test bots`.
