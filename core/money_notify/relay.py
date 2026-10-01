"""core.money_notify.relay — akush-core → bus money.* event relay.

akush-core's intelligence scheduler (§64) emits Phase-6 notification hooks as
financial_events whose payload carries a ``tag`` (a money.* topic string),
surfaced as pending inbox items. The bus is in-process on CT111 and akush-core
(Node, CT108) cannot subscribe in-process, so this relay polls the akush-core
inbox (same API the PWA uses, ``bot`` service token) and publishes each new
tagged item onto the kai event bus, where core.money_notify fans it out.

Guarantees: never raises; restart-safe (last relayed inbox id persisted);
one relay instance only (scheduler process); publishes minimal metadata
payloads (ids/kind/confidence + the item's own payload — the payload already
carries no secrets per akush-core serialization, §17).
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("kai.money_notify.relay")

POLL_INTERVAL = float(os.environ.get("MONEY_RELAY_INTERVAL", "120"))
LIST_LIMIT = 50

_thread: threading.Thread | None = None
_stop = threading.Event()
_started_once = False
_thread_lock = threading.Lock()


def poll_once(client=None, publish=None, *, state=None) -> dict:
    """One relay pass: new pending inbox items → bus. Never raises."""
    try:
        from . import prefs as prefs_mod
        from core.money_telegram.client import get_client

        st = state if state is not None else prefs_mod.load_state()
        last = int(st.get("relay_last_id") or 0)
        client = client or get_client()
        publish = publish or _default_publish

        rc, listing = client.get("/financial-inbox",
                                 {"status": "pending", "limit": LIST_LIMIT})
        if rc != 200 or not isinstance(listing, dict):
            return {"relayed": 0, "reason": "inbox unavailable"}
        rows = listing.get("data") or []
        new_ids = sorted(r["id"] for r in rows
                         if isinstance(r.get("id"), int) and r["id"] > last)
        relayed = 0
        for iid in new_ids:
            dc, detail = client.get(f"/financial-inbox/{iid}")
            if dc != 200 or not isinstance(detail, dict):
                continue
            payload = detail.get("payload") or {}
            topic = payload.get("tag")
            if not topic or not str(topic).startswith("money."):
                continue
            event_payload = {
                "inbox_id": iid,
                "kind": detail.get("kind") or detail.get("event_kind"),
                "confidence": detail.get("confidence"),
                **{k: v for k, v in payload.items() if k != "tag"},
            }
            try:
                publish(str(topic), event_payload, source="money-core-relay")
                relayed += 1
            except Exception:
                logger.warning("relay publish failed for inbox %s", iid)
        if new_ids:
            st["relay_last_id"] = max(new_ids)
            if state is None:
                prefs_mod.save_state()
        return {"relayed": relayed, "last_id": max(new_ids) if new_ids else last}
    except Exception as exc:  # noqa: BLE001
        logger.warning("money relay poll failed: %s", type(exc).__name__)
        return {"relayed": 0, "reason": f"error: {type(exc).__name__}"}


def _default_publish(topic: str, payload: dict, source: str) -> None:
    from core import kai_event_bus
    kai_event_bus.publish(topic, payload, source=source)


def _loop() -> None:
    while not _stop.is_set():
        poll_once()
        _stop.wait(POLL_INTERVAL)


def start_relay() -> bool:
    """Start the relay daemon thread (idempotent, fail-safe)."""
    global _thread, _started_once
    if _started_once and _thread and _thread.is_alive():
        return False
    with _thread_lock:
        if _thread and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="money-relay",
                                   daemon=True)
        _thread.start()
        _started_once = True
        logger.info("money relay started (interval %ss)", POLL_INTERVAL)
        return True
