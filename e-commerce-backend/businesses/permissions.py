"""
Business-scoped permissions. Every class here reads `view.business`, which
only exists once `BusinessScopedMixin.initial()` has run — these are never
used on a view that doesn't include that mixin.

Deliberately separate from `core.permissions.IsStaff`/`IsStaffOrReadOnly`:
those two are the PLATFORM-admin gate (`User.is_staff`) and stay exactly
that — see `businesses/platform_views.py`. Nothing below ever checks
`is_staff`, and nothing in `core.permissions` ever checks a
`BusinessMembership`. A person can be both, independently; neither implies
the other, by design (Decision 1 of the multi-tenant plan).

The three-tier split below follows the approved plan's own wording:
"owner: full business CRUD... + membership management. manager: same
operational access as owner, minus membership management. staff: read
products/inventory, transition orders — cannot edit products/settings/
memberships." Concretely:
  - IsBusinessStaff    — any active role. Reading everything, plus the two
                         fulfilment actions (adjust stock, transition an
                         order) that are explicitly staff's job.
  - IsBusinessManager  — owner or manager. Creating/editing/deleting a
                         product, category, variant, image, inventory
                         record, or store setting.
  - IsBusinessOwner    — owner only. Membership management, business
                         status changes.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import BusinessMembership
from .resolution import active_membership

OWNER, MANAGER, STAFF = (
    BusinessMembership.Role.OWNER,
    BusinessMembership.Role.MANAGER,
    BusinessMembership.Role.STAFF,
)


def caller_role(request, view) -> str | None:
    """The caller's active role in `view.business`, or None if they have no
    active membership there. Exposed for `get_queryset()` methods that need
    to branch on role (e.g. showing draft products to a business's own
    staff) without duplicating the lookup a permission class already did."""
    business = getattr(view, "business", None)
    if business is None:
        return None
    membership = active_membership(request.user, business)
    return membership.role if membership else None


def has_business_role(*roles: str) -> type[BasePermission]:
    """
    `permission_classes = [has_business_role(OWNER, MANAGER)]` — a factory,
    not a class, because DRF instantiates permission classes with no
    arguments; the roles have to be baked into the class object itself
    before that happens.
    """

    class _HasBusinessRole(BasePermission):
        def has_permission(self, request, view):
            return caller_role(request, view) in roles

    return _HasBusinessRole


class IsBusinessMember(BasePermission):
    """Any active role (owner/manager/staff) in the request's business."""

    def has_permission(self, request, view):
        return caller_role(request, view) is not None


class IsBusinessMemberOrReadOnly(BasePermission):
    """Public read — anyone, including an anonymous storefront visitor.
    Writes need an active membership, any role. The business-scoped
    counterpart to core.permissions.IsStaffOrReadOnly."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return caller_role(request, view) is not None


class IsBusinessManagerOrReadOnly(BasePermission):
    """Public read. Writes need owner/manager specifically — staff reads the
    catalog (including this business's own drafts, via get_queryset()) but
    does not manage it."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return caller_role(request, view) in (OWNER, MANAGER)


IsBusinessStaff = has_business_role(OWNER, MANAGER, STAFF)
IsBusinessManager = has_business_role(OWNER, MANAGER)
IsBusinessOwner = has_business_role(OWNER)
