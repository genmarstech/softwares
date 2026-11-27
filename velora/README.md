# Velora backend

Django + DRF API for Velora, the bookings and appointments platform for salons, barbershops, spas,
beauty clinics, nail studios and beauty professionals.

## Run it

```bash
cp .env.example .env          # then fill in SECRET_KEY and the database values
docker compose up -d          # Postgres
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Run the tests with `USE_SQLITE=1 python manage.py test`.

### Background jobs

Two commands should be scheduled (cron or a systemd timer):

| Command | When | What it does |
| --- | --- | --- |
| `python manage.py process_notifications` | every minute | Sends due reminders and follow-ups, retries failed sends, releases unpaid holds |
| `python manage.py send_summary_reports --days 7` | weekly | Emails each business a summary |

## Apps and URL prefixes

| Prefix | App | Purpose |
| --- | --- | --- |
| `idn/api/auth/` | `identity` | `csrf/`, `register/`, `login/`, `logout/`, `me/` (session cookie auth) |
| `bn/` | `business` | `business/`, `services/`, `staff/` |
| `bk/` | `bookings` | Scheduling, bookings, customers, notifications log, analytics, public booking API |
| `cu/` | `customers` | Customer accounts on booking links: `session/`, `register/`, `login/`, `logout/`, `join/`, `bookings/` |

### CSRF

Session-authenticated writes need the `X-CSRFToken` header. `GET idn/api/auth/csrf/` sets the cookie and
returns `{"csrfToken": ...}`. Login, register and `me/` also return the current token, because `login()`
rotates it.

### Tenant isolation

Every business-owned endpoint uses `business.mixins.BusinessScopedMixin`. List calls must pass
`?business=<id>`, which is only resolved among businesses the signed-in user owns. Detail calls only see
the owner's own rows, so ids from another business return 404.

### Owner API (`bk/`, signed in)

- `policy/?business=` GET/PATCH: time zone, slot interval, minimum notice, max booking window,
  cancellation and reschedule windows, deposit and cancellation fee rules, notification channels,
  reminder hours, quiet hours.
- `working-hours/`, `time-off/`, `locations/`, `resources/`: CRUD. Working hours with no `staff` are the
  business defaults; staff rows override them.
- `availability/?business=&service=&date=&staff=`: open slots.
- `bookings/`: list (filters `date_from`, `date_to`, `status`, `staff`, `service`, `customer`, `search`,
  `channel`, `booked_since`; `ordering=start|-start|-created_at`; `limit`),
  create (phone, staff and walk-in bookings), and the actions `confirm/`, `cancel/`, `reschedule/`,
  `complete/`, `no-show/`, `check-in/`, `payments/`, `history/`, `notifications/`.
- `customers/`, `waitlist/`, `notifications/` (delivery log, with `retry/`).
- `analytics/?business=&date_from=&date_to=&staff=&service=&location=` and `analytics/export/`
  (`kind=bookings|summary`, CSV).

### Public API (`bk/public/`, no login)

`<business>` is the business's booking-link slug (`glow-salon`) or, for links shared before slugs
existed, its numeric id. Customers book at `FRONTEND_URL/book/<slug>`, e.g.
`https://getvelora.co.ke/book/glow-salon`.

Slugs are generated from the business name when a business is created (`glow-salon`, then
`glow-salon-2` for a second business with the same name) and existing businesses get one from
migration `business/0007`. Owners can change theirs with `PATCH bn/business/<id>/ {"slug": ...}`:
lowercase letters, numbers and hyphens, at least 3 characters, not all digits, not taken and not a
reserved word such as `dashboard` (see `business/slugs.py`). Changing it breaks the old slug link, but
the numeric-id link keeps working.

- `<business>/`: profile, services, staff names, policy summary.
- `<business>/availability/?service=&date=&staff=` and `<business>/available-days/`.
- `<business>/bookings/` POST: book a slot.
- `<business>/track/` POST: booking funnel step; `<business>/waitlist/` POST.
- `manage/<token>/` GET, plus `cancel/`, `reschedule/`, `confirm/`, `preferences/` POST: customer
  self-service through the private link sent in every message.

## Booking rules

- Statuses: pending, held (waiting for a deposit), confirmed, rescheduled, cancelled, completed, no-show.
  Every change is written to `BookingEvent`, including who did it and whether a policy was overridden.
- Conflicts: bookings lock the staff (and resource) rows with `SELECT ... FOR UPDATE` and re-check
  overlaps, including the service's prep and cleanup buffers, so the same slot cannot be booked twice.
- Customers can cancel or reschedule online only inside the policy windows. Owners can override, and the
  override is logged. Late cancellations and no-shows can carry a fee.

## Notifications

`notifications` stores every message with its status (queued, sent, delivered, failed, skipped,
cancelled), attempts and errors. Failed sends retry after 5, 15 and 45 minutes. Opt-outs and quiet hours
are respected.

- **Email** goes through [Resend](https://resend.com) when `RESEND_API_KEY` is set. Set
  `RESEND_FROM_EMAIL` to a sender on a domain verified in Resend (e.g. `bookings@getvelora.co.ke`).
  Each email carries the plain text and a branded HTML version (`notifications/emails.py`,
  template `notifications/templates/notifications/email.html`). Customer emails lead with the
  business's name; emails to the business show the Velora logo from
  `FRONTEND_URL/brand/email-logo.png` (override with `VELORA_EMAIL_LOGO_URL`).
  Emails show the business name as the sender name, and customer replies go to the business email.
  Without a key, emails are only logged.
- **SMS, WhatsApp and push** use the console provider (log only) until their providers are chosen.
- Every new online booking emails the business inbox (and the assigned staff member) with the
  customer's name, phone, email, service, time and notes.

Providers live in `notifications/providers.py` and can be overridden per channel with the
`NOTIFY_*_PROVIDER` settings.

## Payments

Deposits, balances, cancellation fees and refunds are recorded manually against a booking. A held booking
is confirmed when its deposit is covered. No payment gateway is connected yet.

### Customer accounts (`cu/`)

Customers sign up or sign in on a business's link (`/book/<slug>`) with their phone number and a password.
The phone number is stored in E.164 (`VELORA_DEFAULT_COUNTRY_CODE`, default `254`, turns `0712...` into
`+254712...`) and is unique, so later WhatsApp and Telegram bots can find a customer by number. One account
works at every business; joining a business creates (or links) that business's `Customer` record.

- `session/?business=<slug>` GET: who is signed in and whether they joined that business. PATCH: name/email.
- `register/`, `login/` POST (`business`, `phone_number`, `password`, plus `full_name`, `email` to register):
  sign in and join the business. Throttled per IP (`customer_auth`).
- `join/`, `logout/` POST. `bookings/?business=<slug>` GET: upcoming and past bookings with `rebook` info,
  plus `favourites` for one-tap "book again".
- Signed-in customers post to `bk/public/<slug>/bookings/` without name/phone/email.
- The customer is kept in the session separately from the business-owner login. Writes need `X-CSRFToken`.
- Online bookings now wait as **pending** by default (`auto_confirm` defaults to off for new businesses).
- Setup checklist: "Share your link with 5 customers" counts customers who joined or booked online.
