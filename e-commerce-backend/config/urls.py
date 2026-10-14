"""
URL configuration for the Genmars e-commerce backend.

/admin/     — Django admin. Still works; /api/ops/ below doesn't replace it.
/api/       — scoped to the caller. A customer's own cart, orders, addresses.
/api/ops/   — every record in the company. Staff only (IsAdminUser on every
              view in it), namespaced apart from /api/ for the same reason
              gen-portal keeps its own /api/ops/ separate: so "every order"
              and "my orders" can never be confused in a proxy rule or a log
              line. Added for the admin dashboard frontend — see the
              frontend plan's Part 2.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("cart.urls")),
    path("api/", include("orders.urls")),
    path("api/", include("payments.urls")),
    path("api/ops/", include("core.ops_urls")),
    path("api/ops/", include("orders.ops_urls")),
    path("api/ops/", include("payments.ops_urls")),
    path("api/ops/", include("accounts.ops_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
