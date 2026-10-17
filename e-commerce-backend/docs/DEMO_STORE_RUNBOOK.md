# Demo store runbook

How to stand up a clean, clearly-labeled demo business for showing
prospective pilot customers the storefront and dashboard — and tear it
down again. Every step below was actually run against a real backend this
review, not just written from reading the code; see the Phase 7A report
for what that run proved and the one anomaly it surfaced.

**Naming convention — follow this, don't improvise**: slug `genmars-demo`,
accounts under `@genmars-demo.example`, SKUs prefixed `DEMO-`. The point is
that anyone looking at the database later can tell this is demo content at
a glance, never mistake it for a real business's data, and never need to
guess which rows are safe to delete. Never add demo customers or demo
orders to a real pilot's business — if a second demo is ever needed,
give it its own clearly-named slug, don't reuse a real one "just for a
quick test."

## 1. Administrative account

```bash
./venv/bin/python manage.py createsuperuser
```

This is the one Django-admin-only step every onboarding (demo or real)
currently needs — see `docs/STAGING_ACCEPTANCE_CHECKLIST.md`'s own note on
the bootstrap problem: nothing can assign a business's first owner without
already holding ownership somewhere.

## 2. Demo business + owner

Register the owner's account like any customer would, through the real API
(so their password is never handled by anyone else):

```bash
curl -s -X POST $BASE/api/auth/register/ -H 'Content-Type: application/json' \
  -d '{"email":"demo-owner@genmars-demo.example","password":"<pick one>"}'
```

Then, as the platform admin, create the business and its first owner
membership — either through `/admin/` (`businesses/admin.py`'s
`BusinessMembershipInline`, already covered by
`tests/test_admin_onboarding.py`), or the equivalent one-off:

```bash
./venv/bin/python manage.py shell -c "
from django.contrib.auth import get_user_model
from businesses.services import create_business, add_membership
from businesses.models import BusinessMembership

User = get_user_model()
owner = User.objects.get(email='demo-owner@genmars-demo.example')
biz = create_business(name='Genmars Demo Store', slug='genmars-demo', default_currency='KES')
add_membership(biz, owner, BusinessMembership.Role.OWNER)
"
```

**Expect**: `Business.objects.filter(slug='genmars-demo', status='active')`
exists, with exactly one active owner membership.

## 3. Currency and delivery fee

The dashboard already has a page for this — log in as the owner at
`/login`, then `/dashboard/genmars-demo/settings`, and set currency and
default delivery fee there. (Equivalent API call, if scripting it:
`PATCH /api/ops/genmars-demo/settings/` with the owner's token.)

## 4. Categories, products, variants, images, inventory

**No dashboard page exists for this yet** — see the Phase 7A report's
compatibility findings. Create catalog data via the API, authenticated as
the owner (same permission level `IsBusinessManager` checks for, so this
isn't a special "demo-only" path):

```bash
TOKEN=<owner's access token, from step 2's register response or /api/auth/login/>

curl -s -X POST $BASE/api/genmars-demo/categories/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"Demo Gadgets"}'
# -> note the returned "id", used as "category" below

curl -s -X POST $BASE/api/genmars-demo/products/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Demo Wireless Mouse","sku":"DEMO-001","price":"1200.00",
       "description":"A clearly-labeled demo product.","status":"published","category":<id>}'
# -> note the returned "id", used as "product" below

curl -s -X POST $BASE/api/genmars-demo/inventory/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"product":<id>,"quantity":0}'
# creating an InventoryRecord always starts it at 0 (quantity is read-only
# on create by design — see inventory/serializers.py) — note its "id",
# then actually stock it through the one path that's allowed to change
# quantity:

curl -s -X POST $BASE/api/genmars-demo/inventory/<record id>/adjust/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"delta":25,"reason":"restock","note":"Demo store initial stock"}'

curl -s -X POST $BASE/api/genmars-demo/product-images/ -H "Authorization: Bearer $TOKEN" \
  -F "product=<id>" -F "sort_order=0" -F "image=@/path/to/a/real/image.png"
```

Repeat for however many demo products make a convincing storefront —
two or three is enough to demo browsing, cart, and checkout.

**Confirmed live this review**: the uploaded image's URL
(`http://<host>/media/products/...`) actually resolves — this used to be a
dead link on every deployment (see the Phase 7A report's "Media serving"
finding; fixed in `config/urls.py`).

## 5. Verify the storefront and purchase loop

As a **separate** account (never the owner's — a demo should show the
actual customer path):

```bash
curl -s -X POST $BASE/api/auth/register/ -H 'Content-Type: application/json' \
  -d '{"email":"demo-customer@genmars-demo.example","password":"<pick one>"}'
CTOKEN=<the access token from that response>

curl -s $BASE/api/genmars-demo/products/          # anonymous — storefront browsing
curl -s -X POST $BASE/api/genmars-demo/cart/items/ -H "Authorization: Bearer $CTOKEN" \
  -H 'Content-Type: application/json' -d '{"product":<id>,"quantity":2}'
curl -s -X POST $BASE/api/genmars-demo/checkout/ -H "Authorization: Bearer $CTOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"delivery_recipient_name":"Demo Customer","delivery_line1":"123 Demo Street",
       "delivery_city":"Nairobi","delivery_phone":"0712345678"}'
```

**Expect**: a real order, `status: "awaiting_payment"`, `delivery_fee`
matching what step 3 set, `total` = subtotal + delivery fee.

Through the actual frontend: register/log in as the demo customer at
`/register` → `/login`, browse `/store/genmars-demo`, add to cart, check
out, and open the resulting order at `/store/genmars-demo/orders/<id>`.

## 6. Verify tenant isolation

With the demo customer's token, confirm the other business's data is
completely invisible — **confirmed live this review**:

```bash
curl -s $BASE/api/genmars-store/orders/ -H "Authorization: Bearer $CTOKEN"
# -> {"count": 0, ...} — the demo customer has never ordered from genmars-store

curl -s $BASE/api/genmars-store/products/ | ...   # only genmars-store's own SKUs
curl -s $BASE/api/genmars-demo/products/  | ...   # only DEMO- SKUs
```

## 7. Demonstrate the manual-payment limitation honestly

With `MPESA_ENABLED=false` on the backend (this phase's business decision)
**and** `MPESA_ENABLED=false` set on the frontend deployment (the two are
independent settings that must be kept in sync — see
`src/lib/api/config.ts`'s own comment), the order page shows:

> "This order is awaiting payment. We'll contact you on the phone number
> you provided to arrange payment — no automatic M-Pesa prompt will be
> sent for this order."

**Confirmed live this review**, rendered on the real page — not the "Pay
with M-Pesa" form, which would otherwise call an M-Pesa integration that
isn't configured and fail. See the Phase 7A report's "Manual-payment
behavior" section for what's actually enforced server-side versus what
this is just honest messaging about.

## 8. Tearing a demo down

There is no delete endpoint for a `Business` (by design — see
`businesses/platform_views.py`; suspend, don't delete, is the modeled
lifecycle). To retire a demo: `POST /api/ops/platform/businesses/<id>/suspend/`
(platform admin token required), or remove the rows directly in a
throwaway/dev database. Never do this against a database holding a real
pilot's data without the same care `docs/BACKUP_AND_RECOVERY.md` describes
for any destructive operation.
