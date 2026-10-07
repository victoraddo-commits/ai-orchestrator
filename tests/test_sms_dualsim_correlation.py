"""Dual-SIM forwarder correlation — a per-message receiving number must
resolve to the registered line it belongs to.

Regression guard for the KaiSmsForwarder multi-SIM fix: the Android app now
posts the SIM the SMS actually arrived on as ``to``. These tests prove the
server side correlates a message posted with ``to=+233550109054`` to the
vodafone line (and account), rather than the MTN line or nothing.
"""

import pytest

from core.sms import manager
from core.sms.adapter import RawSms

VODAFONE_LINE = "+233550109054"   # z-fold SIM 2 (MTN Ghana physical is SIM... no: MTN)
MTN_LINE = "+233248077604"


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def lines_and_account():
    manager.register_line(VODAFONE_LINE, carrier="vodafone", label="z-fold SIM")
    manager.register_line(MTN_LINE, carrier="mtn", label="S24 Ultra")
    from core.accounts import AccountCreate, create_account

    account = create_account(AccountCreate(
        provider="vodafone", phone=VODAFONE_LINE, account_type="sms",
        mission_id="mis-dualsim-1", verification_status="PENDING"))
    return account


def _sms(to_number: str, body: str = "Your code is 4821") -> RawSms:
    return RawSms(
        from_number="MTN MoMo",
        to_number=to_number,
        body=body,
        timestamp="2026-10-02T10:00:00+00:00",
    )


def test_vodafone_to_correlates_to_vodafone_line(lines_and_account):
    sms = manager.ingest_raw(_sms(VODAFONE_LINE))
    assert manager._resolve_line(sms.to_number) == VODAFONE_LINE


def test_vodafone_to_correlates_to_vodafone_account(lines_and_account):
    sms = manager.ingest_raw(_sms(VODAFONE_LINE))
    account, mission_id = manager.correlate(sms)
    assert account is not None
    assert account.phone == VODAFONE_LINE
    assert mission_id == "mis-dualsim-1"


def test_mtn_to_does_not_leak_into_vodafone_line(lines_and_account):
    sms = manager.ingest_raw(_sms(MTN_LINE))
    assert manager._resolve_line(sms.to_number) == MTN_LINE
    assert manager._resolve_line(sms.to_number) != VODAFONE_LINE
