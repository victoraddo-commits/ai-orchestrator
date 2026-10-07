"""core.sms security — sender plausibility / short-code / lookalike heuristics."""

from core.sms.security import validate_sender
from core.sms.schema import SenderKind


def test_short_code_valid():
    result = validate_sender("12345")
    assert result.kind == SenderKind.SHORT_CODE
    assert result.suspicious is False


def test_mobile_number_valid():
    result = validate_sender("+233248077604")
    assert result.kind == SenderKind.MOBILE
    assert result.suspicious is False


def test_alphanumeric_sender_flagged_spoofable():
    result = validate_sender("MyBank")
    assert result.kind == SenderKind.ALPHANUMERIC
    assert any("spoof" in n.lower() for n in result.notes)


def test_lookalike_alphanumeric_sender_suspicious():
    result = validate_sender("MyBankk", official_senders=["MyBank"])
    assert result.suspicious is True
    assert any("lookalike" in n.lower() for n in result.notes)


def test_exact_official_sender_not_lookalike():
    result = validate_sender("MyBank", official_senders=["MyBank"])
    assert any("lookalike" in n.lower() for n in result.notes) is False


def test_implausible_short_sender_suspicious():
    result = validate_sender("12")
    assert result.suspicious is True


def test_missing_sender_suspicious():
    result = validate_sender("")
    assert result.suspicious is True
