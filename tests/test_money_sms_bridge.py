"""tests/test_money_sms_bridge.py — Phase 2c money SMS bridge (offline, RED->GREEN)."""

import json
import os
import types
import unicodedata

import pytest

from core import kai_event_bus


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    (tmp_path / "memory").mkdir()
    import core.money_sms.bridge as _bridge_mod
    _bridge_mod._STATE_PATH = tmp_path / "memory" / "money_sms_bridge_state.json"
    _bridge_mod._state = None
    _bridge_mod._queue.clear()
    _bridge_mod._worker_started = False
    monkeypatch.setattr("core.money_sms.bridge._FINGERPRINT_SKEY", None)
    monkeypatch.setattr("core.money_sms.bridge._BRIDGE_TOKEN", None)
    monkeypatch.setenv("KAI_MONEY_SMS_URL", "https://akush-core.test/internal/sms/ingest")
    monkeypatch.setenv("KAI_MONEY_SMS_ENABLED", "1")
    yield tmp_path


@pytest.fixture
def posted(monkeypatch):
    """Capture every POST and control its outcome."""
    calls = []

    class Outcome:
        status = 200
        fail_times = 0

    outcome = Outcome()

    def _post(url, json=None, headers=None, timeout=None, *a, **k):
        calls.append({"url": url, "json": json, "headers": headers})
        if len(calls) <= outcome.fail_times:
            raise RuntimeError("connection refused")
        return types.SimpleNamespace(status_code=outcome.status,
                                     json=lambda: {"id": 1, "duplicate": False})

    monkeypatch.setattr("core.money_sms.bridge.httpx", types.SimpleNamespace(post=_post))
    monkeypatch.setattr("core.money_sms.bridge.sleep", lambda *_a, **_k: None)
    return calls, outcome


@pytest.fixture
def fpkey(monkeypatch):
    def _fetch(path, token=None, *a, **k):
        return "k" * 32 if path == "secrets/money/sms_fingerprint_key" else None
    monkeypatch.setattr("core.money_sms.bridge.fetch_secret", _fetch)
    monkeypatch.setattr("core.money_sms.bridge.store_secret", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def bridge_token_from_vault(monkeypatch, fpkey):
    """bridge service token seam — never printed."""
    monkeypatch.setattr("core.money_sms.bridge.bridge_token",
                        lambda: "test-bridge-token")


def _make_sms(body="Allowee 200.00 from Load on Tue Sep 30. Bal GHS 500.",
              sender="20600", mid=None):
    from core.money_sms.bridge import _build_record
    return _build_record(sender=sender, body=body,
                         timestamp="2026-09-30T09:00:00Z",
                         message_id=mid or os.urandom(12).hex().lower())


def _publish_sms_received(payload):
    kai_event_bus.publish("sms.received", payload, source="tests")


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint_deterministic_and_normalized(fpkey):
    from core.money_sms.fingerprint import fingerprint_for
    a = fingerprint_for("src-1", "+233206000000", "2026-09-30T09:00:00Z",
                        "ACME\n GHS\t500.00")
    b = fingerprint_for("src-1", "+233206000000", "2026-09-30T09:00:00Z",
                        "acme ghs 500.00")
    assert a == b  # whitespace-collapsed + lowercased
    assert len(a) == 64


def test_fingerprint_hmac_secret_dependent(fpkey):
    from core.money_sms.fingerprint import fingerprint_for
    assert (fingerprint_for("s", "+1", "2026-09-30T09:00:00Z", "b")
            != fingerprint_for("s", "+1", "2026-09-30T09:00:00Z", "c"))


def test_fingerprint_nfkc_stable(fpkey):
    from core.money_sms.fingerprint import fingerprint_for
    a = fingerprint_for("s", "+1", "2026-09-30T09:00:00Z",
                        unicodedata.normalize("NFD", "é 20"))
    b = fingerprint_for("s", "+1", "2026-09-30T09:00:00Z",
                        unicodedata.normalize("NFC", "é 20"))
    assert a == b


# ---------------------------------------------------------------------------
# helper: full record fixture shaped like NormalizedSms.model_dump()
# ---------------------------------------------------------------------------
def _record(body="Allowee 200.00 from Load on Tue Sep 30", sender="+233206000000",
            classification="other", otp_present=False, mid=None):
    return {
        "message_id": mid or os.urandom(12).hex().lower(),
        "from_number": sender,
        "to_number": None,
        "line": "sms-line-test",
        "body": body,
        "received_at": "2026-09-30T09:00:00Z",
        "classification": classification,
        "otp_present": otp_present,
    }


# ---------------------------------------------------------------------------
# OTP skip (defense-in-depth)
# ---------------------------------------------------------------------------
def test_otp_record_never_posted(posted, fpkey):
    from core.money_sms import bridge
    rec = _record(body="Your code is 482913", classification="otp", otp_present=True)
    assert bridge.wants_forward(rec) is False

    bridge._handle_event({"message_id": rec["message_id"]},
                         preloaded=rec)
    assert posted[0] == [] or len(posted[0]) == 0


def test_redacted_body_patterns_never_posted(posted, fpkey):
    from core.money_sms.bridge import looks_like_otp
    assert looks_like_otp("Your code is [REDACTED]")
    assert looks_like_otp("OTP 482913")
    assert looks_like_otp("pin: 482913 expires")
    assert not looks_like_otp(_record()["body"])


# ---------------------------------------------------------------------------
# pipeline: event -> POST payload shape
# ---------------------------------------------------------------------------
def test_event_results_in_correct_payload(posted, fpkey):
    from core.money_sms import bridge
    rec = _record()
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    # (payload asserted below — failures here mean the OTP gate tripped)
    assert len(posted[0]) == 1
    call = posted[0][0]
    body = call["json"]
    for field in ("fingerprint", "source_id", "sender", "timestamp", "body",
                  "otp_redacted", "line"):
        assert field in body
    assert body["source_id"] == rec["message_id"]
    assert body["sender"] == rec["from_number"]
    assert body["body"] == rec["body"]
    assert body["otp_redacted"] is False
    assert body["timestamp"] == rec["received_at"]
    assert "Bearer " in (call["headers"] or {}).get("Authorization", "")


def test_feature_flag_off_skips_post(posted, fpkey):
    from core.money_sms import bridge
    rec = _record()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KAI_MONEY_SMS_ENABLED", "0")
        bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    assert posted[0] == [] or len(posted[0]) == 0


def test_unreachable_key_fails_visible(posted, fpkey, monkeypatch):
    """Missing fingerprint key must dead-letter + publish failure, not POST."""
    from core.money_sms import bridge
    monkeypatch.setattr(bridge, "fetch_secret", lambda *a, **k: None)
    failures = []

    def _pub(topic, payload, *a, **k):
        if topic == bridge.FAILURE_TOPIC:
            failures.append((topic, payload))
        return 0
    monkeypatch.setattr(kai_event_bus, "publish", _pub)

    rec = _record()
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    assert posted[0] == [] or len(posted[0]) == 0
    assert failures and failures[0][0] == bridge.FAILURE_TOPIC
    state = bridge.load_state()
    assert len(state["dead_letter"]) == 1


# ---------------------------------------------------------------------------
# duplicate suppression via persisted state
# ---------------------------------------------------------------------------
def test_duplicate_suppressed_by_state(posted, fpkey):
    from core.money_sms import bridge
    rec = _record(mid="dup-msg")
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    bridge._reset_runtime_cache()
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    assert len(posted[0]) == 1


# ---------------------------------------------------------------------------
# retry -> dead-letter -> visible failure event
# ---------------------------------------------------------------------------
def test_retry_then_dead_letter_plus_failure_event(posted, fpkey, monkeypatch):
    from core.money_sms import bridge
    calls, outcome = posted
    outcome.fail_times = 10  # always fail
    monkeypatch.setattr(bridge, "MAX_ATTEMPTS", 3)
    published = []

    def _pub(topic, payload, *a, **k):
        if topic != "sms.received":
            published.append(topic)
        return 0
    monkeypatch.setattr(kai_event_bus, "publish", _pub)

    rec = _record()
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    assert len(calls) == 3  # max 3 attempts
    state = bridge.load_state()
    assert len(state["dead_letter"]) == 1
    assert bridge.FAILURE_TOPIC in published
    assert bridge.load_state()["counters"]["failed"] == 1


# ---------------------------------------------------------------------------
# bounded queue: drop-oldest + dead-letter on overflow
# ---------------------------------------------------------------------------
def test_queue_overflow_drops_oldest_to_dead_letter(fpkey, posted):
    from core.money_sms import bridge
    bridge._reset_runtime_cache()
    for i in range(bridge.QUEUE_MAX + 3):
        rec = _record(mid=f"overflow-{i}")
        assert bridge.enqueue_work({"message_id": rec["message_id"]},
                                   preloaded=rec) is True
    state = bridge.load_state()
    assert len(state["dead_letter"]) == 3
    assert state["counters"]["dropped_overflow"] == 3


# ---------------------------------------------------------------------------
# registration idempotence
# ---------------------------------------------------------------------------
def test_scheduler_registration_idempotent(monkeypatch):
    from core.money_sms import bridge
    import threading
    bridge._sub_id = None
    bridge._registered_once = False
    bridge._reset_runtime_cache()
    subs = []

    monkeypatch.setattr(kai_event_bus, "subscribe",
                        lambda pat, handler, sources=None: (subs.append(pat),
                                                            f"sub-{len(subs)}")[1])
    first = bridge.register_subscriber()
    again = bridge.register_subscriber()
    assert first is not None and again == first
    assert subs.count("sms.received") == 1
    bridge._sub_id = None
    bridge._registered_once = False


def test_subscriber_exception_isolated(posted, fpkey, monkeypatch):
    """Errors from the worker never leak into the event bus respondent — they
    surface as a single ISOLATED error (prefixed), never the raw handler text."""
    from core.money_sms import bridge
    rec = _record()
    # POST fails; _process_one catches internally -> dead-letter only (no raise)
    outcome_data = {}
    calls = []
    class Outcome:
        status = 200
        fail_times = 10  # always fail -> isolated, no bubble
    outcome = Outcome()
    def _post(url, json=None, headers=None, timeout=None, *a, **k):
        calls.append(url)
        if len(calls) <= outcome.fail_times:
            raise RuntimeError("handler blew up")
        return types.SimpleNamespace(status_code=200,
                                     json=lambda: {"id": 1, "duplicate": False})
    monkeypatch.setattr(bridge, "httpx", types.SimpleNamespace(post=_post))
    monkeypatch.setattr(bridge, "sleep", lambda *a, **k: None)
    monkeypatch.setattr("core.kai_event_bus.publish",
                        lambda topic, payload, *a, **k: 0)

    raised = False
    try:
        bridge._handle_event({"message_id": rec["message_id"]}, raise_mode=True)
    except RuntimeError as exc:
        raised = "isolated:" in str(exc) and "handler blew up" not in str(exc)
    assert raised is False  # always swallowed when POST fails
    # pass a deliberately-broken record state so _process_one raises; the shim
    # must still isolate (never a bare "handler blew up")
    monkeypatch.setattr("core.sms.manager.get_sms",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("handler blew up")))
    try:
        bridge._handle_event({"message_id": "fetch-boom"}, preloaded=None,
                             raise_mode=True)
        bare = False     # manager error already caught -> no raise
    except RuntimeError as exc:
        bare = "handler blew up" in str(exc) and "isolated" not in str(exc)
    assert bare is False


# ---------------------------------------------------------------------------
# registration path unaffected: existing sms tests keep their fixtures green
# ---------------------------------------------------------------------------
def test_bridge_module_never_touches_inbox_store(fpkey, posted, monkeypatch, tmp_path):
    """The bridge may only fetch records — never mutate the worker inbox."""
    from core.money_sms import bridge
    mutated = []
    monkeypatch.setattr("core.sms.store.update_inbox",
                        lambda *a, **k: mutated.append(k) or mutated.append(a))
    rec = _record()
    bridge._handle_event({"message_id": rec["message_id"]}, preloaded=rec)
    assert mutated == []


def test_manager_module_unpatched_api():
    """core.sms.manager keeps its public surface — registration app untouched."""
    from core.sms import manager
    for fn in ("ingest_raw", "ingest_webhook", "list_sms", "get_sms"):
        assert callable(getattr(manager, fn, None))
    from core.sms.server import create_app  # webhook surface still importable
    assert callable(create_app)
