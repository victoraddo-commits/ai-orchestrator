"""secret_guard: boolean flags are not secret material; values still are."""

import pytest

from core.secret_guard import SecretFieldError, assert_no_secret_fields, find_secret_fields


def test_boolean_otp_flag_is_allowed():
    # otp_present carries no value; the store must accept it
    assert_no_secret_fields([{"message_id": "sms-1", "otp_present": True}])
    assert find_secret_fields({"otp_present": False}) == []


def test_string_otp_field_still_rejected():
    with pytest.raises(SecretFieldError):
        assert_no_secret_fields({"otp": "482913"})


def test_password_field_still_rejected():
    with pytest.raises(SecretFieldError):
        assert_no_secret_fields({"password": "hunter2"})


def test_nested_secret_still_rejected():
    with pytest.raises(SecretFieldError):
        assert_no_secret_fields({"line": {"vault_token": "abc"}})
