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

- `<business_id>/`: profile, services, staff names, policy summary.
- `<business_id>/availability/?service=&date=&staff=` and `<business_id>/available-days/`.
- `<business_id>/bookings/` POST: book a slot.
- `<business_id>/track/` POST: booking funnel step; `<business_id>/waitlist/` POST.
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
