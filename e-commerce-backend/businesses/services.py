"""
The one place a `Business` is created and a `BusinessMembership` is changed
or removed — same convention as `orders/services.py` and
`accounts/identity.py`: the invariant lives here once, not re-checked (or
forgotten) at every call site.

The invariant that matters: a business can never be left with zero active
owners. Without it, a business could end up with nobody able to manage its
own membership, settings, or staff — effectively locked, with no path back
in short of a Genmars platform admin intervening by hand. `full_clean()` on
create is what makes `Business.slug`'s reserved-word validator actually run;
`Business.objects.create()` alone would skip it.
"""

from __future__ import annotations

from django.db import transaction
from django.utils.text import slugify

from .models import Business, BusinessMembership


class MembershipError(Exception):
    """Raised for any reason a membership cannot be changed or removed.
    `safe_message` is fine to show the business owner who attempted it."""

    def __init__(self, reason: str, safe_message: str):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = safe_message


@transaction.atomic
def create_business(*, name: str, slug: str = "", **fields) -> Business:
    # Slugify here, before full_clean() — Business.save()'s own auto-slug
    # fallback runs too late to help: full_clean() rejects a blank SlugField
    # before save() ever gets a chance to fill it in.
    business = Business(name=name, slug=slug or slugify(name), **fields)
    business.full_clean()
    business.save()
    return business


@transaction.atomic
def add_membership(business: Business, user, role: str, *, invited_by=None) -> BusinessMembership:
    """The one path onto a business's membership list, for the same reason
    `create_business` is the one path onto `Business` — `full_clean()` here
    is what would catch a duplicate membership if a caller somehow bypassed
    the serializer-level check (businesses/serializers.py), as a last
    resort rather than letting a raw IntegrityError through."""
    membership = BusinessMembership(business=business, user=user, role=role, invited_by=invited_by)
    membership.full_clean()
    membership.save()
    return membership


def _has_other_active_owner(business: Business, excluding: BusinessMembership) -> bool:
    return (
        business.memberships.filter(
            role=BusinessMembership.Role.OWNER, status=BusinessMembership.MembershipStatus.ACTIVE
        )
        .exclude(pk=excluding.pk)
        .exists()
    )


def _guard_last_owner(membership: BusinessMembership) -> None:
    """Refuses to proceed if removing/demoting/suspending `membership` would
    leave its business with no active owner at all."""
    if (
        membership.role == BusinessMembership.Role.OWNER
        and membership.status == BusinessMembership.MembershipStatus.ACTIVE
        and not _has_other_active_owner(membership.business, excluding=membership)
    ):
        raise MembershipError(
            "last_owner",
            "This business must keep at least one active owner — "
            "promote someone else first.",
        )


@transaction.atomic
def change_role(membership: BusinessMembership, new_role: str, *, actor=None) -> BusinessMembership:
    locked = BusinessMembership.objects.select_for_update().get(pk=membership.pk)
    if locked.role == BusinessMembership.Role.OWNER and new_role != BusinessMembership.Role.OWNER:
        _guard_last_owner(locked)
    locked.role = new_role
    locked.save(update_fields=["role"])
    return locked


@transaction.atomic
def suspend_membership(membership: BusinessMembership, *, actor=None) -> BusinessMembership:
    locked = BusinessMembership.objects.select_for_update().get(pk=membership.pk)
    _guard_last_owner(locked)
    locked.status = BusinessMembership.MembershipStatus.SUSPENDED
    locked.save(update_fields=["status"])
    return locked


@transaction.atomic
def remove_membership(membership: BusinessMembership, *, actor=None) -> None:
    locked = BusinessMembership.objects.select_for_update().get(pk=membership.pk)
    _guard_last_owner(locked)
    locked.delete()
