"""core.sms normalize — number normalization, sender kind, matching, redaction."""

from core.sms.normalize import (
    normalize_number,
    normalize_sender,
    numbers_match,
    redact_otp,
    sender_kind,
)
from core.sms.schema import SenderKind


def test_normalize_keeps_e164_plus():
    assert normalize_number("+233248077604") == "+233248077604"


def test_normalize_strips_formatting():
    assert normalize_number("+1 (380) 500-3090") == "+13805003090"
    assert normalize_number("+233 24 807 7604") == "+233248077604"


def test_normalize_short_code_preserved():
    assert normalize_number("12345") == "12345"
    assert normalize_number("887456") == "887456"


def test_sender_kind_short_code():
    assert sender_kind("12345") == SenderKind.SHORT_CODE
    assert sender_kind("887456") == SenderKind.SHORT_CODE


def test_sender_kind_alphanumeric():
    assert sender_kind("MyBank") == SenderKind.ALPHANUMERIC


def test_normalize_sender_preserves_alphanumeric():
    assert normalize_sender("MyBank") == "MyBank"
    assert normalize_sender("+1 (380) 500-3090") == "+13805003090"


def test_sender_kind_mobile():
    assert sender_kind("+233248077604") == SenderKind.MOBILE
    assert sender_kind("13805003090") == SenderKind.MOBILE


def test_numbers_match_ignores_formatting_and_country_zero():
    assert numbers_match("+233248077604", "0248077604") is True
    assert numbers_match("+13805003090", "+1 (380) 500-3090") is True
    assert numbers_match("+233248077604", "+233240000000") is False


def test_redact_otp_replaces_code_keeps_rest():
    out = redact_otp("Your verification code is 482913. Do not share.", ["482913"])
    assert "482913" not in out
    assert "[REDACTED]" in out
    assert "Do not share" in out


def test_redact_otp_handles_prefix_form():
    out = redact_otp("G-754321 is your Google verification code", ["754321"])
    assert "754321" not in out


def test_redact_otp_noop_without_codes():
    body = "Your package was delivered."
    assert redact_otp(body, []) == body
