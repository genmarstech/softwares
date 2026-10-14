"""
URL configuration for the Genmars e-commerce backend.

/admin/                — Django admin. Still works; /api/ops/ below doesn't
                         replace it.
/health/               — unauthenticated readiness check (database only —
                         see core/health.py for why). No secrets, no stack
                         traces, ever, in the response body.
/api/                  — identity: auth, me/, addresses/. Global to the
                         user, not business-scoped — see Decision 2 of the
                         multi-tenant plan (one account, many businesses).
                         Also the M-Pesa callback, which Safaricom calls
                         directly and which has no concept of a business
                         slug at all.
/api/<business_slug>/  — that business's storefront: catalog, inventory,
                         cart, checkout, orders, payment-initiate. The slug
                         is a routing key, not a trust boundary — every view
                         under it still checks the caller's own identity or
                         membership; see businesses/resolution.py.
/api/ops/platform/     — Genmars operating the platform itself (creating,
                         activating, suspending businesses). IsAdminUser
                         only, and deliberately the only tree with no
                         business slug in it — it spans all of them.
/api/ops/<business_slug>/
                        — that business's own staff surface: orders,
                         payments, customers, dashboard, settings, members.
                         Access means an active BusinessMembership in THIS
                         business, not the platform-wide is_staff flag —
                         namespaced apart from /api/<slug>/ for the same
                         reason gen-portal keeps its own /api/ops/ separate:
                         so "every order" and "my orders" can never be
                         confused in a proxy rule or a log line. Within it,
                         role still matters — settings and members/ are
                         owner/manager (members/ mutation: owner only); see
                         businesses/permissions.py for the full split.

"platform", "ops", "api", "auth", "me", "addresses", "admin", "static",
"media", "schema", "docs", "health" are all reserved business slugs (see
businesses/models.py::RESERVED_SLUGS) precisely so a real business can never
collide with any of the fixed segments above.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from core.health import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthCheckView.as_view(), name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("payments.urls")),
    path("api/<slug:business_slug>/", include("catalog.urls")),
    path("api/<slug:business_slug>/", include("inventory.urls")),
    path("api/<slug:business_slug>/", include("cart.urls")),
    path("api/<slug:business_slug>/", include("orders.urls")),
    path("api/<slug:business_slug>/", include("payments.business_urls")),
    path("api/ops/platform/", include("businesses.platform_urls")),
    path("api/ops/<slug:business_slug>/", include("core.ops_urls")),
    path("api/ops/<slug:business_slug>/", include("orders.ops_urls")),
    path("api/ops/<slug:business_slug>/", include("payments.ops_urls")),
    path("api/ops/<slug:business_slug>/", include("accounts.ops_urls")),
    path("api/ops/<slug:business_slug>/", include("businesses.ops_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
