import logging

from django.core.mail import send_mail
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from businesses.models import BusinessMembership
from businesses.serializers import MyBusinessMembershipSerializer

from . import identity
from .models import Address
from .serializers import (
    AddressSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)

log = logging.getLogger(__name__)


def _tokens_for(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"
    serializer_class = RegisterSerializer

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = identity.register(**serializer.validated_data)
        except identity.AuthError as exc:
            return Response({"detail": exc.safe_message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"user": UserSerializer(user).data, **_tokens_for(user)},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = identity.authenticate(**serializer.validated_data)
        except identity.AuthError as exc:
            log.info("sign-in failed: %s", exc.reason)
            return Response({"detail": exc.safe_message}, status=status.HTTP_401_UNAUTHORIZED)

        return Response({"user": UserSerializer(user).data, **_tokens_for(user)})


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        refresh = request.data.get("refresh")
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                pass  # Already invalid/expired — logout is still a success.
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MyBusinessesView(APIView):
    """
    The one thing `/api/me/` doesn't answer: which businesses this user is
    an active member of. Filtered by `user=request.user` ONLY — never
    trusts anything from the request to widen it, so there is no id this
    endpoint could be handed that would ever return another user's
    memberships. Not paginated: a person's own membership count is always
    small, and `{count, next, previous, results}` would be a strange shape
    to force onto a handful of rows.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MyBusinessMembershipSerializer

    def get(self, request):
        memberships = (
            BusinessMembership.objects.filter(
                user=request.user, status=BusinessMembership.MembershipStatus.ACTIVE
            )
            .select_related("business")
            .order_by("business__name")
        )
        return Response(MyBusinessMembershipSerializer(memberships, many=True).data)


class PasswordResetRequestView(APIView):
    """
    Always answers the same way, whether or not the address has an account —
    the response itself must not be the enumeration oracle that
    identity.request_password_reset() already refuses to be.
    """

    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issued = identity.request_password_reset(serializer.validated_data["email"])
        if issued:
            send_mail(
                subject="Your password reset code",
                message=f"Your code is {issued.code}. It expires in 15 minutes.",
                from_email=None,
                recipient_list=[issued.user.email],
            )
        return Response({"detail": identity.GENERIC_RESET_MESSAGE})


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            identity.confirm_password_reset(**serializer.validated_data)
        except identity.AuthError as exc:
            return Response({"detail": exc.safe_message}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Password updated. Sign in with your new password."})


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
