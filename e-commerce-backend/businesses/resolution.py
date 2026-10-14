"""
Where a request's business is decided — the one place, so it is never
re-implemented (or forgotten) per view. A business slug in a URL is a
routing key, not a trust boundary by itself: resolving it only ever narrows
what a view can see down to one business; it is never, on its own, proof
that the caller may act on that business's behalf. Every view still ANDs
this against the caller's own identity (a customer's `user=`) or an active
`BusinessMembership` (the ops surface) — see businesses/permissions.py and
businesses/mixins.py for where those checks actually run.
"""

from __future__ import annotations

from django.http import Http404

from .models import Business, BusinessMembership


def resolve_business_or_404(slug: str | None) -> Business:
    """
    404, not some other status, for a missing/suspended/offboarding
    business — the same reasoning as every other "does this id belong to
    you" check in this codebase (see orders/views.py's own comment on it):
    a business that exists but isn't yours to see must look identical to
    one that doesn't exist at all.
    """
    try:
        return Business.objects.get(slug=slug, status=Business.Status.ACTIVE)
    except Business.DoesNotExist as exc:
        raise Http404("No such business.") from exc


def active_membership(user, business: Business) -> BusinessMembership | None:
    """The caller's active membership in `business`, or None. Never raises —
    "not a member" is a permission decision for the caller to make (403),
    not a 404 (the business itself was already resolved and does exist)."""
    if user is None or not user.is_authenticated:
        return None
    return BusinessMembership.objects.filter(
        business=business, user=user, status=BusinessMembership.MembershipStatus.ACTIVE
    ).first()
