# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Django 6.1 + DRF backend for a **multi-tenant commerce platform** (originally
a single-store MVP; `46f1fdd` turned it into a platform where many
independent "businesses" share one Django instance). It lives inside
`~/Genmars/softwares/`, a sibling product, not one of the four core Genmars
repos described at `~/Genmars/CLAUDE.md` — read that file too if you need the
company-wide rules it documents (identity-boundary pattern, snapshot
semantics, the "no second identity for Genmars people" rule, etc.), since this
app's conventions mirror gen-portal's on purpose in several places.

Stack: Postgres, Redis (Celery broker + cache), JWT auth
(`djangorestframework-simplejwt`), DRF + `django-filter` + `drf-spectacular`,
M-Pesa Daraja via stdlib `urllib` (no SDK dependency — Charter 03 §I, see
`payments/mpesa.py`).

### Frontend: `~/Genmars/frontend-reusable-modules/commerce-platform`

That Next.js app is the storefront/BFF for **this** backend (dev port 3040;
run against `API_BASE_URL=http://localhost:8010`, server-only — never
`NEXT_PUBLIC_`). Its `docs/API_CONTRACT.md` is a living, verified-against-
source record of every endpoint this backend exposes, each entry marked
**LIVE** (actually called) or **INSPECTED** (read from source, not called) —
check it before assuming what a response shape looks like from the frontend
side, and update it there (not here) when an endpoint's behavior changes.
Auth storage there is JWTs in the response body over bearer tokens; no cookie
session, no CSRF requirement for the API.

The sibling `~/Genmars/frontend-reusable-modules/e-commerce` module is a
**different, now-superseded single-tenant backend's** frontend (BFF pattern,
httpOnly cookies) — not this repo's. Don't confuse the two when searching
that monorepo for "the frontend."

Known gaps the frontend has already flagged against this backend, still true
unless this CLAUDE.md or the code says otherwise:
- `config/urls.py` never wires `django.conf.urls.static.static(...)` for
  `MEDIA_ROOT` — a `ProductImage.image` URL the API returns is not actually
  reachable in local/dev. Fixing this is a real, scoped bug, not a design
  decision recorded anywhere above.
- No public (anonymous) endpoint returns a `Business`'s own display name,
  currency, or contact info by slug — only `/api/me/businesses/`
  (authenticated, caller's own memberships) and the `is_staff`-only platform
  admin views touch `Business` fields at all.
- `PageNumberPagination`'s `page_size_query_param` is unset, so `?page_size=`
  is silently ignored on every list endpoint — `PAGE_SIZE=20` always wins.

## Commands

```bash
cp .env.example .env                       # already done for this checkout
docker compose up -d                       # Postgres only — see README re: Redis
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver 8010

./venv/bin/python -m pytest                              # full suite, --reuse-db
./venv/bin/python -m pytest tests/test_orders.py::test_name   # one test
./venv/bin/python -m pytest -k "mpesa"                    # by keyword

./venv/bin/celery -A config worker -l info   # low-stock alerts, order-status emails
```

Redis: this machine already runs one instance (shared with another local
project); `.env`'s `REDIS_URL` points at a separate DB index (`/5`) so the two
projects' keys don't collide — `docker-compose.yml` deliberately has no
`redis` service.

No live M-Pesa STK push has ever been fired against this code, and firing one
is a decision for the user, not something to do as part of a task — see
`payments/mpesa.py` and `.env`'s `MPESA_ENABLED`.

## Architecture

### URL namespace: four trees, one Django

```
/api/                    identity only — auth, me/, addresses/, M-Pesa callback.
                          Global to the user, not business-scoped (one account,
                          many businesses — see businesses/models.py).
/api/<business_slug>/    a storefront: catalog, inventory, cart, checkout,
                          orders, payment-initiate. The slug is a routing key,
                          not a trust boundary by itself (see below).
/api/ops/platform/       Genmars operating the platform itself (create,
                          activate, suspend a Business). IsAdminUser
                          (User.is_staff) only — the only tree with no
                          business slug, because it spans all of them.
/api/ops/<business_slug>/
                          that business's own staff surface: orders, payments,
                          customers, dashboard, settings, members. Gated by an
                          active BusinessMembership in THIS business, never by
                          is_staff.
```

`businesses/models.py::RESERVED_SLUGS` (`api`, `ops`, `platform`, `admin`,
`auth`, `me`, `addresses`, `schema`, `docs`, `static`, `media`, `health`) is
the whole reason `Business.slug` has a validator instead of just a uniqueness
constraint — a business taking one of these would shadow a fixed route or
make its own storefront unreachable. `config/urls.py` has the full routing
table and is the first place to look when adding an endpoint; get the
`/api/<slug>/` vs `/api/ops/<slug>/` tree wrong and you've either leaked every
business's data into a customer-facing view or hidden a staff feature behind
customer auth.

### `User.is_staff` and `BusinessMembership` are independent, on purpose

A platform admin (`is_staff=True`) has **no** access to any one business's
storefront or ops surface unless they separately hold a `BusinessMembership`
there — nothing in `businesses/` ever checks `is_staff`, and the platform
views (`businesses/platform_views.py`) never check `BusinessMembership`. A
founder who is also a platform admin has both, independently; neither grants
the other. This is "Decision 1" of the multi-tenant plan, referenced in
comments throughout `businesses/`. There is deliberately no impersonation
path ("log in as this business") — a platform admin who needs access gets a
membership the normal way.

### Tenant resolution: `businesses/resolution.py` + `businesses/mixins.py`

`BusinessScopedMixin.initial()` resolves `self.business` from the URL's
`business_slug` kwarg, via `resolve_business_or_404`, *before* DRF's own
`perform_authentication`/`check_permissions` run — that ordering is what lets
a permission class safely read `view.business`. A missing, suspended, or
offboarding business 404s (never some other status) — same enumeration-oracle
reasoning as everywhere else in this codebase: a business that exists but
isn't yours to see must look identical to one that doesn't exist.

**The slug alone is never proof of authorization.** Every view under
`/api/<slug>/` and `/api/ops/<slug>/` still ANDs the resolved business against
the caller's own identity (`user=` for customer views) or an active
`BusinessMembership` (ops views) — see `businesses/permissions.py`.

### Three-tier role split (`businesses/permissions.py`)

- `IsBusinessStaff` (owner/manager/staff) — read everything, plus the two
  fulfilment actions that are explicitly staff's job: adjust stock, transition
  an order.
- `IsBusinessManager` (owner/manager) — create/edit/delete a product,
  category, variant, image, inventory record, or store setting.
- `IsBusinessOwner` (owner only) — membership management, business status
  changes.
- `*OrReadOnly` variants exist where a storefront needs public/anonymous reads
  (`IsBusinessMemberOrReadOnly`, `IsBusinessManagerOrReadOnly`).

`caller_role(request, view)` is the one lookup every one of these uses —
branch on it in a `get_queryset()` (e.g. showing a business's own drafts to
its staff) instead of re-querying `BusinessMembership` directly.

### `businesses/services.py` — the only path onto `Business`/`BusinessMembership`

`create_business()` routes through `full_clean()` so the reserved-slug
validator actually runs (`Business.objects.create()` alone would skip it).
`change_role`/`suspend_membership`/`remove_membership` all guard against
leaving a business with zero active owners (`_guard_last_owner`) — without
it a business could end up with nobody able to manage its own membership,
recoverable only by a platform admin intervening by hand.

### Inventory: one write path (`inventory/services.py::adjust_stock`)

Every stock change — order creation, cancellation/refund restoration, admin
restock — goes through this one function. It locks the row
(`select_for_update`), refuses to let quantity go negative, and writes an
append-only `InventoryMovement` for every change, so "what happened to this
product's stock" is always answerable from the movement log, never trusted to
whoever last edited the row directly.

### Orders: `orders/services.py`

- `create_order_from_cart()` re-reads price and availability from
  `Product`/`ProductVariant` at checkout time — the cart's cached
  `unit_price` is display-only and never trusted. Locks every
  `InventoryRecord` the checkout touches, in stable `pk` order, before
  reading or writing any of them (avoids two concurrent checkouts
  deadlocking on opposite lock order).
- Stock is deducted **at order creation**, not at add-to-cart and not
  deferred until payment succeeds — a published-but-unpaid order can hold
  stock while `awaiting_payment`. Cancelling/refunding restores it.
- `transition()` is the only path that changes `Order.status` —
  `ALLOWED_TRANSITIONS` is the full state machine; anything not listed raises
  `OrderError`. Entering `CANCELLED`/`REFUNDED` restores stock exactly once
  (`stock_restored` flag prevents double-restoration from a retried/duplicate
  call).
- `OrderItem` rows are a snapshot — nothing about them is re-derived from
  `Product` after creation, same "preserve purchase history" reasoning as
  gen-portal's `Contract`/`Invoice`/`Offer` snapshots.

### M-Pesa (`payments/mpesa.py`)

Two hard rules, stated in the module's own banner: never log the consumer
secret, passkey, or generated password (base64 of the passkey — itself a live
credential); and this module never marks a payment successful — an STK
response means "we sent the prompt," not "they paid." Only the callback
(`payments/views.py`) decides that, and the callback is authenticated by a
pre-shared secret as the final URL path segment (`MPESA_CALLBACK_SECRET`) —
Daraja signs nothing, and an unconfigured secret means every callback is
rejected, never accepted open.

### Identity (`accounts/identity.py`)

Mirrors gen-portal's identity-boundary pattern: every auth operation
(register, sign in, password reset) goes through this one module, and
`AuthError.safe_message` is identical regardless of whether the email exists
— distinguishing "no such account" from "wrong password" is a free
account-enumeration oracle. Sign-in lockout is in here too
(`MAX_FAILED_SIGN_INS` / `LOCKOUT_DURATION`), separate from the per-business
role system entirely — a locked-out or deactivated `User` can't act in *any*
business regardless of membership.

### `core.exceptions.safe_exception_handler`

The global DRF exception handler. DRF's default already keeps
`ValidationError`/`Http404`/etc. safe; this catches everything else (a bug, a
third-party error) so it never reaches Django's bare-HTML 500 page or leaks
`str(exc)` (which can carry a DB error with a table/column name, a file path)
to the client — always logs the real exception server-side, always returns
the same generic `detail` message to the caller.

### The `default_business_id` bridge — temporary, not a design choice

`businesses/models.py::default_business_id()` and `core/models.py`'s
`_default_business_id()` exist only because several models (`Category`,
`Product`, `InventoryRecord`, `Cart`, `Order`, `StoreSettings`) had `business`
added as a required field in the same migration that backfilled every
existing row onto the single seeded business (`SEEDED_BUSINESS_SLUG =
"genmars-store"`). The comments on both functions say explicitly: once every
call site passes `business=` explicitly, this default should come out
entirely rather than staying as a silent "assume the first business"
fallback in a system meant to hold many. Don't add a new call site that
relies on it — pass `business` explicitly.

### Throttling

Only three endpoints opt into `ScopedRateThrottle` (`auth`, `payment-initiate`,
`checkout` — see `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` in
`config/settings.py`): the credential-guessing surface and the two places that
can move real stock or money repeatedly if hammered. Everything else is
unthrottled at the DRF layer — a known MVP-era gap, not an oversight to
silently "fix" by throttling everything.

### `STORAGES["default"]` is explicit on purpose

Customizing `STORAGES` at all (for `staticfiles`, below) stops Django from
merging in its own default `"default"` entry — leaving that key out doesn't
fall back to `FileSystemStorage`, it breaks every `FileField`/`ImageField`
save with a `Could not find config for 'default'` error. Keep both keys if
you touch `STORAGES`.

## Testing conventions

Fixtures live in `tests/conftest.py`: `business` is the migration-seeded
tenant (`genmars-store`), `second_business` is a deliberately separate one for
real cross-tenant isolation tests (as opposed to two names for the same row).
Each role (`business_owner_client`, `business_manager_client`,
`business_staff_client`, and the `second_business_owner_client` counterpart)
gets its **own** `APIClient()` instance — never `force_authenticate()` the
shared `api_client` fixture twice in one test, or you silently swap which user
it's authenticated as. `staff_client`/`staff_user` is a platform admin with
*no* `BusinessMembership` anywhere — use it specifically to prove `is_staff`
alone grants nothing at the business level.

A reference an attacker could guess (an order id, a membership id) returns
404, not 403, across this codebase — a 403 confirms the row exists, which is
the same enumeration oracle in a different costume.

## Docs

`docs/PRODUCTION_ENVIRONMENT.md`, `docs/BACKUP_AND_RECOVERY.md`, and
`docs/STAGING_ACCEPTANCE_CHECKLIST.md` cover deployment-time configuration,
the backup/restore story (no restore script, by design — restoring is rare
and destructive enough to stay a deliberate human-run command), and the
checklist a human runs against a real deployed staging instance (not
pytest) before go-live. Read these before touching deploy-related settings
rather than re-deriving the reasoning.

## Handling secrets

- `.env` holds M-Pesa and backup-encryption configuration. Never print values
  from it.
- `payments/mpesa.py` must never log the consumer secret, passkey, or
  generated password.
- No live M-Pesa STK push has ever been fired — that first call is the
  user's, not yours.
