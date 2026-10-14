import pytest

from accounts import identity


@pytest.mark.django_db
def test_login_generic_message_does_not_reveal_whether_email_exists(customer):
    try:
        identity.authenticate("customer@example.com", "wrong-password")
    except identity.AuthError as exc:
        wrong_password_message = exc.safe_message
    else:
        pytest.fail("expected AuthError")

    try:
        identity.authenticate("nobody@example.com", "wrong-password")
    except identity.AuthError as exc:
        unknown_email_message = exc.safe_message
    else:
        pytest.fail("expected AuthError")

    assert wrong_password_message == unknown_email_message


@pytest.mark.django_db
def test_account_locks_after_max_failed_attempts(customer):
    for _ in range(identity.MAX_FAILED_SIGN_INS):
        with pytest.raises(identity.AuthError):
            identity.authenticate(customer.email, "wrong")

    with pytest.raises(identity.AccountLocked):
        identity.authenticate(customer.email, "a-strong-passw0rd!")


@pytest.mark.django_db
def test_register_rejects_duplicate_email(customer):
    with pytest.raises(identity.AuthError) as exc_info:
        identity.register(email=customer.email, password="another-strong-pw1")
    assert exc_info.value.reason == "email_taken"


@pytest.mark.django_db
def test_password_reset_round_trip(customer):
    issued = identity.request_password_reset(customer.email)
    assert issued is not None

    identity.confirm_password_reset(customer.email, issued.code, "brand-new-passw0rd!")

    customer.refresh_from_db()
    assert customer.check_password("brand-new-passw0rd!")
    # old password no longer works
    with pytest.raises(identity.AuthError):
        identity.authenticate(customer.email, "a-strong-passw0rd!")


@pytest.mark.django_db
def test_password_reset_for_unknown_email_issues_nothing():
    assert identity.request_password_reset("nobody@example.com") is None
