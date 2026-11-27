from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RegistrationSerializer


def user_payload(user):
    return {
        "id": user.id,
        "email": user.email,
        "name": getattr(user, "full_name", ""),
        "phone_number": getattr(user, "phone_number", ""),
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(APIView):
    """Sets the ``csrftoken`` cookie and returns the token.

    The frontend calls this on start-up and keeps the token in memory, so every
    POST/PATCH/DELETE can send ``X-CSRFToken`` even when the cookie lives on a
    different origin and ``document.cookie`` cannot read it.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class RegistrationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return Response(
                {
                    "message": "User Registered Successfully",
                    "authenticated": True,
                    "user": user_payload(user),
                    # login() rotates the CSRF token, so hand the new one back.
                    "csrfToken": get_token(request),
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)
            return Response(
                {
                    "message": "Login successful.",
                    "authenticated": True,
                    "user": user_payload(user),
                    "csrfToken": get_token(request),
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"message": "Invalid email or password.", "error": "Invalid email or password.", "authenticated": False},
            status=status.HTTP_400_BAD_REQUEST,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class getCurrentUser(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response(
                {"authenticated": False, "user": None, "csrfToken": get_token(request)},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"authenticated": True, "user": user_payload(user), "csrfToken": get_token(request)},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(
            {"message": "Logged out successfully.", "csrfToken": get_token(request)},
            status=status.HTTP_200_OK,
        )
