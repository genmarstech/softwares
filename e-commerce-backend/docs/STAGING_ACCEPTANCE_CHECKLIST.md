# Staging acceptance checklist

A human runs this against a real deployed staging instance, over real HTTP
— not the pytest suite (which already covers the same properties against
an in-process test client; this is "does the real deployed thing actually
behave this way," which is a different claim). Replace `$BASE` with the
staging URL throughout (e.g. `https://staging-api.genmars.co.ke`).

Every step says what to expect. If reality disagrees with the expectation,
that's a staging blocker — stop and investigate before continuing, not
after everything else has already been clicked through.

**M-Pesa note**: anywhere this checklist initiates a payment, use Daraja
**sandbox** credentials only, and get explicit separate sign-off before
ever pointing this at production M-Pesa credentials — no live STK push has
been fired at any point in this project, and this checklist doesn't change
that policy.

## 0. Setup — two businesses, four roles each, shared customer

```bash
# A platform admin (is_staff=True Django user) must already exist —
# create one via `manage.py createsuperuser` against the staging DB if
# this is a fresh deploy.
PLATFORM_TOKEN=$(curl -s -X POST $BASE/api/auth/login/ \
  -d '{"email":"<platform-admin-email>","password":"<password>"}' \
  -H 'Content-Type: application/json' | jq -r .access)

# Create both businesses.
curl -s -X POST $BASE/api/ops/platform/businesses/ \
  -H "Authorization: Bearer $PLATFORM_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Staging Business A","slug":"staging-biz-a"}'
curl -s -X POST $BASE/api/ops/platform/businesses/ \
  -H "Authorization: Bearer $PLATFORM_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Staging Business B","slug":"staging-biz-b"}'
```
**Expect**: both return `201`, `"status": "active"`.

Register five accounts via `POST $BASE/api/auth/register/` (email/password):
`owner-a@test`, `manager-a@test`, `staff-a@test`, `owner-b@test`,
`shared-customer@test`. For each staff/manager/owner account, the first
owner must invite them via the members endpoint (§6) — a fresh business has
no members until its creator does this, since the platform-admin create
step above doesn't attach anyone. **Bootstrap problem to watch for**: if
neither business has an owner yet, nobody can invite anyone through
`/members/` (owner-only). Create the first owner's membership directly in
the admin (`/admin/`) or via a one-off `manage.py shell` call for the very
first owner of each business; every subsequent invite goes through the API
normally.

Log in as each account and keep its token handy for the rest of this
checklist — `owner_a`, `manager_a`, `staff_a`, `owner_b`, `customer`.

## 1. Catalog — independent per business

- `owner_a` creates a category and a product (`POST /api/staging-biz-a/categories/`, `.../products/`). **Expect** `201`.
- `owner_b` creates a category and product **with the identical name and SKU** as business A's. **Expect** `201` — this is the exact bug fixed in the consolidated review; if it 400s here, that fix regressed.
- Anonymous `GET /api/staging-biz-a/products/` and `GET /api/staging-biz-b/products/`. **Expect**: each lists only its own business's products, never the other's.
- `staff_a` attempts `POST /api/staging-biz-a/products/`. **Expect** `403` — staff doesn't manage the catalog.
- `manager_a` attempts the same. **Expect** `201`.

## 2. Carts and checkout — independent per business, same customer

- As `customer`, `POST /api/staging-biz-a/cart/items/` with business A's product. **Expect** `201`.
- As `customer`, `POST /api/staging-biz-b/cart/items/` with business B's product. **Expect** `201` — a different cart, not an error, not merged with A's.
- `GET /api/staging-biz-a/cart/` and `GET /api/staging-biz-b/cart/` as `customer`. **Expect**: each shows only its own item.
- `POST /api/staging-biz-a/checkout/` with delivery details. **Expect** `201`, an order under business A.
- `POST /api/staging-biz-b/checkout/` similarly. **Expect** `201`, a separate order under business B, business A's stock untouched.

## 3. Inventory — role split, per-business

- `staff_a`: `POST /api/staging-biz-a/inventory/{record_id}/adjust/` with a delta. **Expect** `200` — adjusting stock is staff's job.
- `staff_a`: `POST /api/staging-biz-a/inventory/` (creating a new inventory record). **Expect** `403`.
- `manager_a`: the same create. **Expect** `201`.
- `owner_a` (or any business-A role): `GET /api/staging-biz-a/inventory/{business-B-record-id}/` using a real record id copied from business B's own inventory list. **Expect** `404`, not the record.

## 4. Orders and payments

- `staff_a`: `POST /api/ops/staging-biz-a/orders/{order_id}/transition/` with a valid next status for one of business A's own paid orders. **Expect** `200`.
- `owner_a`: `GET /api/ops/staging-biz-a/orders/{business-B-order-id}/` using a real id from business B. **Expect** `404`.
- As `customer`, `POST /api/staging-biz-a/payments/mpesa/initiate/` with business A's own order reference. **Expect** `202` (sandbox credentials only — see the note at the top).
- As `customer`, repeat against `/api/staging-biz-b/payments/mpesa/initiate/` using **business A's** order reference. **Expect** `404` — the slug doesn't make someone else's order payable.
- `owner_a`: `GET /api/ops/staging-biz-a/payments/{business-B-payment-id}/`. **Expect** `404`.

## 5. Customer reports

- Confirm `shared-customer@test` has placed one order at each business (from §2).
- `owner_a`: `GET /api/ops/staging-biz-a/customers/`. **Expect**: the shared customer appears, with `order_count: 1` and `lifetime_total` equal to only business A's order — not both.
- `owner_b`: the equivalent call for business B. **Expect**: same customer, but `order_count`/`lifetime_total` reflecting only business B's order.

## 6. Settings and membership management

- `staff_a`: `GET /api/ops/staging-biz-a/settings/`. **Expect** `403`.
- `manager_a`: the same. **Expect** `200`, and a `PATCH` to change `default_delivery_fee` **Expect** `200`.
- `owner_b`: `GET /api/ops/staging-biz-a/settings/` (wrong business, but a real owner elsewhere). **Expect** `403` — a valid, active slug grants nothing without membership there.
- `owner_a`: `POST /api/ops/staging-biz-a/members/` inviting `manager-a@test`/`staff-a@test` with the appropriate role. **Expect** `201` each.
- `manager_a`: attempt the same invite for a new user. **Expect** `403` — manager doesn't manage membership.
- `owner_a`: attempt to `DELETE` their own membership (as the sole owner, before any second owner exists). **Expect** `400`, membership survives.

## 7. Platform administration stays separate

- `owner_a`: `GET /api/ops/platform/businesses/`. **Expect** `403` — owning a business is not platform admin.
- The platform admin token: the same call. **Expect** `200`, listing both businesses.
- Confirm nowhere in this checklist did any payload containing `is_staff` succeed in setting it — there is no field for it on any business-facing endpoint to even attempt.

## 8. Operational checks (Phase 4B/4C, against the real deployment)

- `GET $BASE/health/` with no auth. **Expect** `200`, body exactly `{"status": "ok"}` — nothing else.
- Any response's headers include `X-Request-ID`.
- Attempt 21 checkouts in quick succession as one customer. **Expect** the 21st to return `429`.
- Attempt to upload a >5MB (or non-image) file as a product image via `manager_a`. **Expect** `400` in both cases.

## 9. Final regression check

Run the automated suite against the same codebase one more time before
signing off:
```bash
./venv/bin/python -m pytest -q
./venv/bin/python manage.py check --deploy --fail-level WARNING
```
**Expect**: full pass, and only the documented `mail.E001` finding (or
none, if production email is already configured on this environment).

## Sign-off

This checklist passing is evidence for "the deployed instance behaves the
way the code says it should." It is not evidence that backups are
scheduled, that monitoring exists, or that a real M-Pesa sandbox round trip
has been completed — those are tracked separately (see
`docs/BACKUP_AND_RECOVERY.md` and the outstanding-risks list in the Phase 4
report).
