"""core.sms OTP hand-off — in-memory only, TTL, single-use, zeroize."""

import time

from core.sms.otp import OtpHandoffStore, handoffs


def _store(clock=None):
    if clock is None:
        return OtpHandoffStore(ttl=60.0)
    return OtpHandoffStore(ttl=60.0, clock=clock)


def test_stash_and_consume_once():
    store = _store()
    token = store.stash("482913", mission_id="mis-1")
    assert store.consume(token) == "482913"
    # single use — second consume returns nothing
    assert store.consume(token) is None


def test_consume_for_mission():
    store = _store()
    store.stash("482913", mission_id="mis-1")
    assert store.consume_for(mission_id="mis-1") == "482913"
    assert store.consume_for(mission_id="mis-1") is None


def test_consume_for_account():
    store = _store()
    store.stash("654321", account_id="acct-1", mission_id="mis-1")
    assert store.consume_for(account_id="acct-1") == "654321"


def test_pending_returns_token_not_code():
    store = _store()
    token = store.stash("482913", mission_id="mis-1")
    assert store.pending(mission_id="mis-1") == token
    assert store.pending(mission_id="nope") is None


def test_expiry_before_consume():
    fake = {"t": 1000.0}
    store = _store(clock=lambda: fake["t"])
    store.stash("482913", mission_id="mis-1", ttl=10.0)
    fake["t"] += 11.0
    assert store.consume_for(mission_id="mis-1") is None


def test_zeroize_clears_internal_buffer():
    store = _store()
    token = store.stash("482913", mission_id="mis-1")
    assert store._debug_code_bytes(token) == b"482913"
    store.consume(token)
    assert store._debug_code_bytes(token) is None


def test_module_singleton_is_store():
    assert isinstance(handoffs, OtpHandoffStore)
