"""
`/api/ops/<business_slug>/members/` — this business's own membership roster.

Owner-only for every mutation (invite, change role, remove), per the plan:
"owner: ...membership management. manager: same operational access as
owner, minus membership management." Staff and manager can both list the
roster (any active member can see who's on the team); neither can touch it.
"""

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS

from businesses.mixins import BusinessScopedMixin
from businesses.permissions import IsBusinessOwner, IsBusinessStaff

from .models import BusinessMembership
from .serializers import BusinessMembershipSerializer
from .services import MembershipError, add_membership, change_role, remove_membership, suspend_membership


class OpsMembershipViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = BusinessMembershipSerializer
    search_fields = ["user__email", "user__first_name", "user__last_name"]

    def get_permissions(self):
        permission_class = IsBusinessStaff if self.request.method in SAFE_METHODS else IsBusinessOwner
        return [permission_class()]

    def get_queryset(self):
        return BusinessMembership.objects.filter(business=self.business).select_related("user")

    def perform_create(self, serializer):
        try:
            membership = add_membership(
                self.business,
                serializer.validated_data["user"],
                serializer.validated_data["role"],
                invited_by=self.request.user,
            )
        except Exception as exc:  # full_clean()'s ValidationError, as a last-resort backstop
            raise ValidationError(str(exc)) from exc
        serializer.instance = membership

    def perform_update(self, serializer):
        # Routed through the service functions rather than a plain
        # serializer.save() — that's what makes the last-active-owner
        # guard (businesses/services.py) impossible to bypass through this
        # endpoint. `user` is deliberately never reassignable here: nothing
        # below ever applies it, even if present in the request body.
        instance = serializer.instance
        new_role = serializer.validated_data.get("role")
        new_status = serializer.validated_data.get("status")

        try:
            if new_role and new_role != instance.role:
                instance = change_role(instance, new_role, actor=self.request.user)
            if new_status and new_status != instance.status:
                if new_status == BusinessMembership.MembershipStatus.ACTIVE:
                    instance.status = new_status
                    instance.save(update_fields=["status"])
                elif new_status == BusinessMembership.MembershipStatus.SUSPENDED:
                    # Same last-owner guard REMOVE uses — suspending the sole
                    # owner is exactly as dangerous as removing them.
                    instance = suspend_membership(instance, actor=self.request.user)
                else:
                    instance.status = new_status
                    instance.save(update_fields=["status"])
        except MembershipError as exc:
            raise ValidationError({"detail": exc.safe_message}) from exc

        serializer.instance = instance

    def perform_destroy(self, instance):
        try:
            remove_membership(instance, actor=self.request.user)
        except MembershipError as exc:
            raise ValidationError({"detail": exc.safe_message}) from exc
