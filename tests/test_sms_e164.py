"""core.sms — E.164 normalization + line correlation (00/011/formatting)."""

import pytest

from core.sms import manager, normalize
from core.sms.adapter import RawSms
from core.sms.schema import Carrier


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


# -- to_e164 unit surface ------------------------------------------------------

def test_to_e164_examples_from_real_traffic():
    assert normalize.to_e164("+0013805003090") == "+13805003090"
    assert normalize.to_e164("0013805003090") == "+13805003090"
    assert normalize.to_e164("+233248077604") == "+233248077604"
    assert normalize.to_e164("+233 24 807 7604") == "+233248077604"
    assert normalize.to_e164("(380) 500-3090", default_cc=1) == "+13805003090"


def test_to_e164_strips_spaces_dashes_parens_dots():
    assert normalize.to_e164("+1 (380) 500.3090") == "+13805003090"
    assert normalize.to_e164("+1-380-500-3090") == "+13805003090"


def test_to_e164_us_international_prefix_011():
    assert normalize.to_e164("011 44 20 7946 0958") == "+442079460958"


def test_to_e164_handles_empty():
    assert normalize.to_e164(None) == ""
    assert normalize.to_e164("") == ""
    assert normalize.to_e164("   ") == ""


def test_to_e164_national_with_default_cc():
    assert normalize.to_e164("3805003090", default_cc=1) == "+13805003090"
    assert normalize.to_e164("0248077604", default_cc=233) == "+233248077604"


def test_to_e164_does_not_double_country_code():
    assert normalize.to_e164("+233248077604", default_cc=233) == "+233248077604"


def test_normalize_number_fixes_double_zero_after_plus():
    assert normalize.normalize_number("+0013805003090") == "+13805003090"
    assert normalize.normalize_number("0013805003090") == "+13805003090"


def test_normalize_number_keeps_short_code():
    assert normalize.normalize_number("12345") == "12345"


# -- manager line correlation --------------------------------------------------

def test_ingest_correlates_double_zero_to_registered_line():
    manager.register_line("+13805003090", carrier="tello")
    sms = manager.ingest_raw(RawSms(from_number="+233248077604",
                                    to_number="+0013805003090",
                                    body="hello"))
    assert sms.to_number == "+13805003090"
    assert sms.line == "+13805003090"
    assert sms.carrier == Carrier.TELLO


def test_register_line_normalizes_double_zero():
    line = manager.register_line("+0013805003090", carrier="tello")
    assert line.number == "+13805003090"
    assert manager.resolve_carrier("+13805003090") == Carrier.TELLO


def test_ingest_unregistered_line_keeps_normalized_number():
    sms = manager.ingest_raw(RawSms(from_number="12345",
                                    to_number="+15551234567",
                                    body="hello"))
    assert sms.to_number == "+15551234567"
    assert sms.line == "+15551234567"
