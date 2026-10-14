from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsStaff(BasePermission):
    """Staff-only writes. Used on catalog/inventory management endpoints."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsStaffOrReadOnly(BasePermission):
    """Anyone — including an anonymous visitor browsing the catalog before
    registering — can read; only staff can write."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
