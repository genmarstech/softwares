"""
The identity boundary.

Every authentication operation — register, sign in, password reset — goes
through this module. Views and serialisers call the functions below and never
touch `User.objects.create_user`, `check_password`, or `VerificationCode`
directly. Keeping it in one place is what makes it possible to audit, and
what makes a future identity-service migration a rewrite of this file only.

Failure handling is deliberately uniform: callers get an `AuthError` with a
`reason` for logs and a `safe_message` for the client, and `safe_message` is
the SAME whether or not the email exists. Distinguishing "no such account"
from "wrong password" is a free account-enumeration oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .models import Address, User, VerificationCode

MAX_FAILED_SIGN_INS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

GENERIC_SIGN_IN_FAILURE = "That email address and password do not match."
GENERIC_RESET_MESSAGE = (
    "If that email address has an account, a reset code has been sent to it."
)


class AuthError(Exception):
    def __init__(self, reason: str, safe_message: str = GENERIC_SIGN_IN_FAILURE):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = safe_message


class AccountLocked(AuthError):
    def __init__(self, until):
        super().__init__(
            "account_locked",
            "Too many failed attempts. Try again in a few minutes — "
            "the lock clears on its own.",
        )
        self.until = until


@dataclass(frozen=True)
class IssuedCode:
    user: User
    code: str


# ── Registration ──────────────────────────────────────────────────────────


def register(
    *,
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    phone_number: str = "",
) -> User:
    email = (email or "").strip().lower()
    if User.objects.filter(email=email).exists():
        # Registration is not sign-in: telling someone an address is already
        # registered does not create an enumeration oracle the way a login
        # failure reason would, since no password is involved.
        raise AuthError("email_taken", "An account with that email already exists.")

    return User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
    )


# ── Sign in ────────────────────────────────────────────────────────────────


def authenticate(email: str, password: str) -> User:
    """Verify credentials. Raises AuthError on any failure."""
    email = (email or "").strip().lower()
    user = User.objects.filter(email=email).first()

    if user is None:
        # Burn a hash so a missing account isn't measurably faster to reject.
        check_password(password or "", make_password("timing-equaliser"))
        raise AuthError("unknown_email")

    if user.is_locked:
        raise AccountLocked(user.locked_until)

    if not user.is_active:
        raise AuthError("inactive_account")

    if not user.check_password(password or ""):
        _register_failure(user)
        if user.is_locked:
            raise AccountLocked(user.locked_until)
        raise AuthError("bad_password")

    _clear_failures(user)
    return user


def _register_failure(user: User) -> None:
    user.failed_sign_ins += 1
    if user.failed_sign_ins >= MAX_FAILED_SIGN_INS:
        user.locked_until = timezone.now() + LOCKOUT_DURATION
        user.failed_sign_ins = 0
    user.save(update_fields=["failed_sign_ins", "locked_until"])


def _clear_failures(user: User) -> None:
    if user.failed_sign_ins or user.locked_until:
        user.failed_sign_ins = 0
        user.locked_until = None
        user.save(update_fields=["failed_sign_ins", "locked_until"])


# ── Password reset ──────────────────────────────────────────────────────────


def request_password_reset(email: str) -> IssuedCode | None:
    """
    Issue a reset code, or do nothing if the address doesn't exist.

    Returns None for an unknown address so the view can send the SAME
    response either way — the caller must not let "None vs IssuedCode"
    change what the client sees.
    """
    email = (email or "").strip().lower()
    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        return None

    _, code = VerificationCode.issue(user, VerificationCode.Purpose.RESET)
    return IssuedCode(user=user, code=code)


@transaction.atomic
def confirm_password_reset(email: str, code: str, new_password: str) -> User:
    email = (email or "").strip().lower()
    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        raise AuthError("unknown_email", "That code is invalid or has expired.")

    record = (
        VerificationCode.objects.select_for_update()
        .filter(user=user, purpose=VerificationCode.Purpose.RESET, used_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if record is None or not record.is_valid:
        raise AuthError("no_valid_code", "That code is invalid or has expired.")

    if not record.matches(code):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise AuthError("wrong_code", "That code is invalid or has expired.")

    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])

    user.set_password(new_password)
    user.failed_sign_ins = 0
    user.locked_until = None
    user.save(update_fields=["password", "failed_sign_ins", "locked_until"])
    return user
