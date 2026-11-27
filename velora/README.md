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
- `bookings/`: list (filters `date_from`, `date_to`, `status`, `staff`, `service`, `customer`, `search`),
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
are respected. Every channel currently uses the **console provider**, which only logs the message.
Real email, SMS, WhatsApp and push providers plug into `notifications/providers.py` and are chosen per
channel with the `NOTIFY_*_PROVIDER` settings.

## Payments

Deposits, balances, cancellation fees and refunds are recorded manually against a booking. A held booking
is confirmed when its deposit is covered. No payment gateway is connected yet.
