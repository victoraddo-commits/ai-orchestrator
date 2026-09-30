"""core.money_sms.bridge — Phase 2c money SMS bridge (project spec §15/§70).

Subscribes (once, fail-safely, exception-isolated) to ``sms.received`` on the
kai event bus. For every non-OTP, non-redacted message the bridge:

  1. fetches the full record via ``core.sms.manager.get_sms`` (metadata-only
     event payloads carry *no body* on purpose),
  2. skips OTP-shaped content (defense-in-depth mirror of akush-core 422),
  3. computes the §15 HMAC dedup fingerprint,
  4. POSTs to akush-core ``/internal/sms/ingest`` with the ``bridge`` service
     token (retries ×3, exponential backoff),
  5. on final failure: dead-letters locally AND publishes
     ``money.sms.bridge_failed`` so the Command Center can show it (§70 must
     be visible, not silent).

Guarantees: never blocks or mutates the SMS worker ingest path; no direct DB
writes (akush-core API is the only write path); state survives restarts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from core.ai import kai_vault_client as _vault_client
from core.sms.detections import detect_otp as _detect_otp

fetch_secret = _vault_client.fetch_secret
store_secret = _vault_client.store_secret


logger = logging.getLogger("kai.money_sms")

SOURCE = "money_sms_bridge"
SUCCESS_TOPIC = "money.sms.forwarded"
FAILURE_TOPIC = "money.sms.bridge_failed"
ENABLED_ENV = "KAI_MONEY_SMS_ENABLED"

REDACTION_MARKERS = ("[REDACTED]",)
QUEUE_MAX = 64
MAX_ATTEMPTS = 3
DEDUP_LRU_MAX = 512
DEAD_LETTER_MAX = 100

# patch points (kept module-level for test seams)
_STATE_PATH = Path("/opt/ai-orchestrator/memory/money_sms_bridge_state.json")
_FINGERPRINT_SKEY: object = None
_BRIDGE_TOKEN: object = None

_state_lock = threading.Lock()
_state: dict | None = None
_runtime_cache: OrderedDict = OrderedDict()
_queue: list = []
_queue_lock = threading.Lock()
_sub_id: str | None = None
_registered_once = False
_worker_started = False


# ---------------------------------------------------------------------------
# state store (restart-safe replay)
# ---------------------------------------------------------------------------
def load_state() -> dict:
    global _state
    with _state_lock:
        if _state is None:
            try:
                _state = json.loads(_STATE_PATH.read_text() or "{}")
            except Exception:
                _state = {}
            _state.setdefault("last_processed_id", None)
            _state.setdefault("fingerprint_lru", [])
            _state.setdefault("dead_letter", [])
            _state.setdefault("counters", {
                "forwarded": 0, "duplicates": 0, "failed": 0,
                "skipped_otp": 0, "dropped_overflow": 0})
        return _state


def _persist_state() -> None:
    state = load_state()
    lru = list(_runtime_cache.keys())[-DEDUP_LRU_MAX:]
    state["fingerprint_lru"] = lru
    state["dead_letter"] = state["dead_letter"][-DEAD_LETTER_MAX:]
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=1))
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-sms state persist failed: %s", type(exc).__name__)


def _persist_state_safe() -> None:
    try:
        _persist_state()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# vault-sourced secrets (bearer + HMAC key) — never logged
# ---------------------------------------------------------------------------
def bridge_token() -> str | None:
    forced = _fingerprint_test_seam()
    return forced or bridge_token_from_vault()


def _fingerprint_test_seam():
    return None


def bridge_token_from_vault() -> str | None:
    global _BRIDGE_TOKEN
    if _BRIDGE_TOKEN:
        return _BRIDGE_TOKEN
    token = _vault_client.load_token()
    if not token:
        return None
    try:
        raw = fetch_secret("secrets/money/service_tokens", token=token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-sms service-token fetch failed: %s", type(exc).__name__)
        raw = None
    if raw:
        try:
            _BRIDGE_TOKEN = (json.loads(raw) or {}).get("bridge")
        except Exception:
            _BRIDGE_TOKEN = None
    return _BRIDGE_TOKEN


def fingerprint_key() -> str:
    global _FINGERPRINT_SKEY
    if _FINGERPRINT_SKEY:
        return _FINGERPRINT_SKEY
    token = _vault_client.load_token()
    if not token:
        raise RuntimeError("vault token unavailable")
    val = None
    try:
        val = fetch_secret("secrets/money/sms_fingerprint_key", token=token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-sms fingerprint-key fetch failed: %s", type(exc).__name__)
    if not val:
        # provision once: generate + store in vault, never print
        import secrets as _secrets
        val = _secrets.token_hex(32)
        try:
            store_secret("secrets/money/sms_fingerprint_key", val, token=token,
                         reason="money sms bridge fingerprint key provisioning")
            val = _vault_client.fetch_secret("secrets/money/sms_fingerprint_key") or val
        except Exception as exc:  # noqa: BLE001
            logger.warning("money-sms fingerprint-key provisioning failed: %s",
                           type(exc).__name__)
            raise RuntimeError("fingerprint key unavailable") from exc
    if not val:
        raise RuntimeError("fingerprint key unavailable")
    _FINGERPRINT_SKEY = val
    return val


# ---------------------------------------------------------------------------
# event -> record fetch
# ---------------------------------------------------------------------------
def _build_record(*, sender: str, body: str, timestamp: str, message_id: str):
    """Offline test seam: shape a record like NormalizedSms.model_dump()."""
    return {
        "message_id": message_id, "from_number": sender, "to_number": None,
        "line": "sms-line-test", "body": body, "received_at": timestamp,
        "classification": "other", "otp_present": False,
    }


def _fetch_record(message_id: str | None):
    import core.sms.manager as manager
    if not message_id:
        return None
    try:
        record = manager.get_sms(str(message_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-sms record fetch failed: %s", type(exc).__name__)
        return None
    return record.model_dump(mode="json") if record else None


def _enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# dedup + gate
# ---------------------------------------------------------------------------
def _remember_mark(fp: str) -> bool:
    """Add fp to the runtime LRU. True if new, False if duplicate."""
    if fp in _runtime_cache:
        return False
    _runtime_cache[fp] = time.time()
    while len(_runtime_cache) > DEDUP_LRU_MAX:
        _runtime_cache.popitem(last=False)
    return True


def looks_like_otp(body: str) -> bool:
    """True if a body still looks like it carries a live OTP code, or was
    redacted upstream (``[REDACTED]``). Mirrors akush-core's 422 check so an
    OTP-shaped body NEVER leaves CT111."""
    text = str(body or "")
    if any(marker in text for marker in REDACTION_MARKERS):
        return True
    if bool(_detect_otp(text)):
        return True
    import re as _re
    return bool(_re.search(
        r"(\b(?:code|otp|pin)\b)[^0-9]{0,24}\b\d{4,8}\b",
        text, _re.IGNORECASE))


def wants_forward(record: dict) -> bool:
    """Defense-in-depth OTP gate — mirrors akush-core's 422."""
    if not isinstance(record, dict):
        return False
    if record.get("otp_present") or record.get("classification") == "otp":
        return False
    return not looks_like_otp(record.get("body", ""))


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------
def sleep(seconds: float) -> None:  # test seam
    time.sleep(seconds)


def _post_to_akush(payload: dict, *, max_attempts: int | None = None) -> dict | None:
    if max_attempts is None:
        max_attempts = globals()["MAX_ATTEMPTS"]
    if looks_like_otp(payload.get("body", "")):
        raise RuntimeError("otp-shaped body refused at transport (defense-in-depth)")
    token = bridge_token()
    if not token:
        raise RuntimeError("bridge service token unavailable")
    base = os.environ.get("KAI_MONEY_SMS_URL", "http://192.168.1.118:8095/internal/sms/ingest")
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            response = httpx.post(
                base, json=payload, timeout=10.0,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                verify=False)
            if response.status_code == 200:
                return response.json()
            last_exc = RuntimeError(f"akush-core HTTP {response.status_code}")
        except RuntimeError as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if attempt < max_attempts - 1:
            sleep(delay)
            delay *= 2.0
    raise last_exc or RuntimeError("akush-core POST failed")


# ---------------------------------------------------------------------------
# failure visibility (§70)
# ---------------------------------------------------------------------------
def _publish_failure(payload: dict, reason: str, *, dead_lettered: bool) -> None:
    from core import kai_event_bus
    try:
        kai_event_bus.publish(FAILURE_TOPIC, {
            "reason": reason, "dead_lettered": dead_lettered,
            "message_id": (payload or {}).get("source_id"),
        }, source=SOURCE, severity="critical")
    except Exception:
        logger.exception("money-sms failure event publish failed")


# ---------------------------------------------------------------------------
# worker pool
# ---------------------------------------------------------------------------
def enqueue_work(event: dict, *, preloaded: dict | None = None) -> bool:
    """Small bounded FIFO, drop-oldest + dead-letter on overflow."""
    global _worker_started
    ev = {"event": event, "preloaded": preloaded}
    with _queue_lock:
        if len(_queue) >= globals()["QUEUE_MAX"]:
            dropped = _queue.pop(0)
            _queue.append(ev)
            state = load_state()
            state["dead_letter"].append({
                "ts": time.time(), "kind": "queue_overflow",
                "message_id": (dropped.get("event") or {}).get("message_id"),
            })
            state["counters"]["dropped_overflow"] = int(
                state["counters"].get("dropped_overflow", 0)) + 1
            _persist_state()
            _publish_failure({}, "queue_overflow_drop_oldest", dead_lettered=True)
        else:
            _queue.append(ev)
    if preloaded is None and not _worker_started and not _is_test_context():
        _worker_started = True
        threading.Thread(target=_worker_loop, name="money-sms-bridge",
                         daemon=True).start()
    return True


def _is_test_context() -> bool:
    return False


def _worker_loop() -> None:
    while True:
        work = []
        while True:
            with _queue_lock:
                if not _queue:
                    break
                work.append(_queue.pop(0))
            for item in work:
                _process_one(item["event"], item.get("preloaded"))
            work = []
        time.sleep(0.5)


def _process_one(event: dict, preloaded: dict | None) -> None:
    if not globals().get("_BRIDGE_TOKEN", None) and os.environ.get(
            "KAI_MONEY_SMS_FORCE_FAKE_TOKEN") == "1":
        globals()["_BRIDGE_TOKEN"] = "fake-bridge-token"
    states = load_state()
    token_probe = bridge_token()
    if not token_probe:
        # without the service token nothing can be forwarded: dead-letter +
        # publish-visible-failure (never silently swallowed)
        states["dead_letter"].append({
            "ts": time.time(), "message_id": (event or {}).get("message_id"),
            "reason": "bridge_token_unavailable"})
        states["counters"]["failed"] = int(states["counters"].get("failed", 0)) + 1
        _persist_state()
        _publish_failure({"source_id": (event or {}).get("message_id")},
                         "bridge_token_unavailable", dead_lettered=True)
        return
    state = states
    message_id = (event or {}).get("message_id")
    if preloaded is not None:
        record = preloaded
    else:
        record = _fetch_record(message_id)
    if not record:
        return

    # -- feature flag (default ON) -------------------------------------------
    if not _enabled():
        return

    # -- OTP gate (never transmit) ------------------------------------------
    if not wants_forward(record):
        state["counters"]["skipped_otp"] = int(
            state["counters"].get("skipped_otp", 0)) + 1
        _persist_state_safe()
        return

    # -- fingerprint dedup ----------------------------------------------------
    fp = _compute_fingerprint(record)
    if fp is None:
        state["counters"]["failed"] = int(state["counters"].get("failed", 0)) + 1
        _dead_letter(record, "fingerprint_failed")
        return
    if fp in load_state().get("fingerprint_lru", []):
        s2 = load_state()
        s2["counters"]["duplicates"] = int(
            s2["counters"].get("duplicates", 0)) + 1
        s2["last_processed_id"] = record.get("message_id")
        _runtime_cache[fp] = time.time()
        while len(_runtime_cache) > DEDUP_LRU_MAX:
            _runtime_cache.popitem(last=False)
        _persist_state_safe()
        return
    if not _remember_mark(fp):
        state["counters"]["duplicates"] = int(
            state["counters"].get("duplicates", 0)) + 1
        state["last_processed_id"] = record.get("message_id")
        _persist_state_safe()
        return

    # -- POST ------------------------------------------------------------------
    payload = {
        "fingerprint": fp,
        "source_id": record.get("message_id"),
        "sender": record.get("from_number"),
        "timestamp": record.get("received_at"),
        "body": record.get("body", ""),
        "otp_redacted": bool(record.get("otp_present")),
        "line": record.get("line"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        _post_to_akush(payload)
    except Exception as exc:  # noqa: BLE001
        state["counters"]["failed"] = int(state["counters"].get("failed", 0)) + 1
        _dead_letter(record, f"post_failed: {type(exc).__name__}")
        return
    state["counters"]["forwarded"] = int(state["counters"].get("forwarded", 0)) + 1
    state["last_processed_id"] = record.get("message_id")
    _persist_state_safe()
    try:
        from core import kai_event_bus as _bus
        _bus.publish(SUCCESS_TOPIC, {"message_id": record.get("message_id")},
                     source=SOURCE)
    except Exception:
        logger.exception("money-sms success event publish failed")


def _compute_fingerprint(record: dict) -> str | None:
    from .fingerprint import fingerprint_for
    try:
        return fingerprint_for(record.get("message_id"), record.get("from_number"),
                               record.get("received_at"), record.get("body", ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-sms fingerprint failed: %s", type(exc).__name__)
        return None


def _dead_letter(record: dict, reason: str) -> None:
    state = load_state()
    state["dead_letter"].append({
        "ts": time.time(), "message_id": record.get("message_id"),
        "reason": reason,
    })
    _persist_state()
    _publish_failure({"source_id": record.get("message_id")}, reason,
                     dead_lettered=True)


# ---------------------------------------------------------------------------
# registration (idempotent, exception-isolated, fail-safe)
# ---------------------------------------------------------------------------
def register_subscriber():
    """Attach the bridge subscriber. Idempotent; never raises. Reads the bus's
    real subscriber list first so a re-bound bus instance never double-fires."""
    global _sub_id, _registered_once
    if _registered_once and _sub_id:
        return _sub_id
    try:
        from core import kai_event_bus as _bus
        try:
            existing = [
                sid for sid, sub in getattr(_bus.event_bus, "_subscribers",
                                            {}).items()
                if sub.get("pattern") == "sms.received"
                and sub.get("handler") is _on_event_safe
            ]
        except Exception:
            existing = []
        if existing:
            _sub_id = existing[0]
    except Exception:
        pass
    if _sub_id is None:
        try:
            from core import kai_event_bus as _bus
            _sub_id = _bus.subscribe("sms.received", _on_event_safe,
                                     sources={"sms_worker"})
            _registered_once = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("money-sms registration failed: %s", type(exc).__name__)
            _sub_id = None
    return _sub_id


def _on_event_safe(topic: str, envelope) -> None:
    """Exception-isolated pub path: the bus must never see bridge errors."""
    event = envelope.get("payload") if isinstance(envelope, dict) else envelope
    if not isinstance(event, dict):
        event = {"message_id": str(event)}
    try:
        enqueue_work(event)
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("money-sms processing error: %s", type(exc).__name__)
        except Exception:
            pass


def _test_handle(event: dict, *, preloaded: dict | None = None,
                 raise_mode: bool = False) -> None:
    """Public synchronous entry for tests (does not start the pool)."""
    try:
        _process_one(event, preloaded)
    except Exception as exc:  # noqa: BLE001
        if raise_mode:
            raise RuntimeError(f"isolated: {exc}") from exc


def _handle_event(event: dict, *, preloaded: dict | None = None,
                  raise_mode: bool = False) -> None:
    """Consumer/test shim. With ``raise_mode`` only isolated exceptions bubble
    (bridging itself always catches) — matches event-bus isolation contract."""
    try:
        _test_handle(event, preloaded=preloaded)
    except Exception as exc:  # noqa: BLE001
        if raise_mode:
            raise RuntimeError(f"isolated: {type(exc).__name__}")
        logger.warning("money-sms handling error: %s", type(exc).__name__)


def _reset_runtime_cache() -> None:
    _runtime_cache.clear()


__all__ = [
    "register_subscriber", "enqueue_work", "load_state", "wants_forward",
    "bridge_token", "fingerprint_key", "FAILURE_TOPIC", "SUCCESS_TOPIC",
    "QUEUE_MAX", "MAX_ATTEMPTS",
]
