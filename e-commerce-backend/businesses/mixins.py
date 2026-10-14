"""
`BusinessScopedMixin` resolves `self.business` from the URL's
`business_slug` kwarg before anything else runs — `initial()` is where
`APIView.dispatch()` calls `perform_authentication()`, then
`check_permissions()`, then `check_throttles()`, in that order, so setting
`self.business` as the very first thing in an overridden `initial()` (before
calling `super()`) is what makes it safe for a permission class to read
`view.business` and for `get_queryset()` to filter on it — both run later in
the same dispatch, never before this.

A 404 from `resolve_business_or_404` (a missing/suspended business) is a
plain `django.http.Http404`, which DRF's own exception handler already
turns into a 404 response — nothing extra to catch here.
"""

from .resolution import resolve_business_or_404


class BusinessScopedMixin:
    business = None

    def initial(self, request, *args, **kwargs):
        self.business = resolve_business_or_404(kwargs.get("business_slug"))
        super().initial(request, *args, **kwargs)

    def get_serializer_context(self):
        # So a serializer can run its own per-business uniqueness checks
        # (catalog/serializers.py's `validate_name`/`validate_sku`, e.g.) at
        # validation time, before perform_create() would otherwise be the
        # first place `business` is known to it.
        context = super().get_serializer_context()
        context["business"] = self.business
        return context
