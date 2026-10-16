# Genmars E-commerce Backend

Django 6.1 + DRF API for a minimal e-commerce store: catalog, inventory,
cart, checkout, orders, M-Pesa payments, and admin operations. Built against
`genmars_ecommerce_backend_mvp_checklist.pdf`.

## Stack

Postgres, Redis (Celery broker), JWT auth (`djangorestframework-simplejwt`),
DRF + `django-filter` + `drf-spectacular`, M-Pesa Daraja via stdlib `urllib`
(no SDK dependency — see `payments/mpesa.py`).

## Local setup

```bash
cp .env.example .env            # already done for this checkout; edit as needed
docker compose up -d            # Postgres only — see note below on Redis
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py createsuperuser
./venv/bin/python manage.py runserver 8010
```

**Redis**: this machine already runs one (for the `json-formatter` project)
on the default port. `.env`'s `REDIS_URL` points at it on DB index 5 so the
two projects' keys don't collide — `docker-compose.yml` deliberately doesn't
start a second one. If you're setting this up somewhere without an existing
redis, add a `redis` service back to `docker-compose.yml`.

To run background tasks (low-stock alerts, order-status emails):

```bash
./venv/bin/celery -A config worker -l info
```

To run the daily scheduled job (the commerce-metrics report to gen-portal —
see "Platform reporting" below), also run the beat process:

```bash
./venv/bin/celery -A config beat -l info
```

## Tests

```bash
./venv/bin/python -m pytest
```

24 tests covering: the sign-in enumeration oracle and lockout, atomic stock
decrement under concurrency (10 threads racing 5 units of stock), checkout's
price/availability re-validation, order status transition rules, stock
restoration on cancellation, M-Pesa webhook idempotency (duplicate delivery
is a no-op), and cross-customer order access (404, not 403).

## API surface

- `/admin/` — Django admin. The entire staff/admin-operations surface for
  v1: products, categories, images, variants, stock adjustments (via an
  admin action that goes through `inventory.services.adjust_stock`, never a
  raw field edit), orders (search, fulfillment-status actions, inline
  status history), payments (read-only audit view), shipments.
- `/api/...` — the customer-facing API. `/api/docs/` is a Swagger UI over
  the live schema (`/api/schema/`).
- `/api/payments/mpesa/callback/` — Safaricom calls this server-to-server.
  Not reachable from a browser; see `payments/views.py`.

## M-Pesa

`MPESA_ENABLED=False` by default — payments are off until real Daraja
credentials are in `.env`. **No live STK push has been fired against this
code.** That first call is yours, not something run on your behalf.

## Platform reporting

This backend is a sibling product to Genmars' core repos (see this repo's
own CLAUDE.md). A daily job computes a platform-wide commerce-metrics
snapshot (acquisition, revenue, storefront funnel, inventory, retention —
see analytics/metrics.py for the exact shape and what it can and cannot
honestly compute yet) and POSTs it to gen-portal, Genmars' own core Django
backend, so the numbers show up in the company's internal ops dashboard.

- `GENMARS_API_ORIGIN` / `GENMARS_SYSTEM_KEY` — see .env.example. The key is
  a bearer token, issued by hand from gen-portal's own ops UI, and does not
  exist until a founder issues one after this ships. Treat it exactly like
  the M-Pesa secrets below: never commit it, never log it.
- Runs daily via Celery beat (`CELERY_BEAT_SCHEDULE` in config/settings.py);
  also runnable by hand: `./venv/bin/python manage.py report_commerce_metrics`.
- A missing `GENMARS_SYSTEM_KEY` fails loudly (one log line, non-zero exit)
  and safely (no raise that reaches the Celery worker loop, no retry storm)
  — see analytics/reporting.py.

## Checklist coverage

Mirrors `genmars_ecommerce_backend_mvp_checklist.pdf` section by section.

| # | Section | Status |
|---|---|---|
| 1 | Authentication & access | Done — `accounts/identity.py`, JWT, lockout, password reset |
| 2 | Product catalog | Done — `catalog/` |
| 3 | Inventory & stock | Done — `inventory/`, atomic `adjust_stock()` |
| 4 | Cart & checkout | Done — `cart/`, server-side re-pricing in `orders/services.py` |
| 5 | Orders | Done — `orders/`, snapshot `OrderItem`, status history |
| 6 | Payments | Done — M-Pesa STK + idempotent webhook. No live push fired yet. |
| 7 | Customer records | Done — `accounts.Address`, own-order-history only |
| 8 | Admin operations | Done — Django admin only, no separate staff API (see plan) |
| 9 | Delivery & fulfillment | Done — `delivery.Shipment`, status-change emails |
| 10 | API & database | Done — DRF conventions, migrations, indexes |
| 11 | Security & reliability | Done — see hardening in `config/settings.py`; backups via `scripts/backup.sh` (tested restore) |
| 12 | Testing & launch | Done — `pytest`; monitoring/CI not set up (no hosting target chosen yet) |

Explicitly out of scope (per the checklist's own scope guard): multi-vendor
marketplace, microservices, AI recommendations, loyalty programs, advanced
analytics, subscriptions. Guest checkout is also out — accounts are required.

## What's next, by priority

1. Real Daraja sandbox credentials in `.env`, then a real STK push (yours to
   fire).
2. A reconciliation job for `Payment`s stuck `pending` (Daraja's Transaction
   Status Query API) — the checklist's "reconciliation" line is otherwise
   only covered by the callback path.
3. Pick a hosting target and wire up CI + the health-check/monitoring half
   of "launch readiness" — this repo has no deploy pipeline yet.
