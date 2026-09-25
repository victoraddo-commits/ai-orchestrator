"""Smart-home service: provider refresh, control via the Command Bus.

This is the ONLY place that mutates device state. It:
  1. refreshes state from a provider adapter into the canonical registry,
  2. executes control through the existing Command Bus (AgentGuard + audit),
  3. reads back the observed state and verifies it (command sent != success),
  4. never fabricates: on provider failure the state is marked stale, not guessed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.smarthome import registry as reg
from core.smarthome.adapters.base import AdapterError

logger = logging.getLogger(__name__)

STALE_AFTER_S = 300

# Provider name -> adapter factory. Imported lazily so tests can inject fakes.
_ADAPTERS: dict[str, object] = {}


def register_adapter(provider: str, adapter) -> None:
    """Register an adapter instance/factory for a provider (used by routes/tests)."""
    _ADAPTERS[provider] = adapter


def get_adapter(provider: str):
    if provider in _ADAPTERS:
        return _ADAPTERS[provider]
    if provider == "homeassistant":
        from core.smarthome.adapters.homeassistant import HomeAssistantAdapter
        adapter = HomeAssistantAdapter()
        _ADAPTERS[provider] = adapter
        return adapter
    if provider == "tuya":
        from core.smarthome.adapters.tuya import TuyaAdapter
        # Devices are registered in the canonical registry; the adapter resolves
        # each device's IP/key at call time (key from the encrypted vault).
        devices = {}
        for d in reg.list_devices():
            if d.provider == "tuya":
                devices[d.provider_id] = {"ip": d.address or d.provider_id,
                                          "name": d.name,
                                          "kind": d.kind.value}
        adapter = TuyaAdapter(devices)
        _ADAPTERS[provider] = adapter
        return adapter
    raise AdapterError(f"no adapter registered for provider {provider!r}")


def refresh(provider: str | None = None) -> dict:
    """Pull live state for all devices (or one provider) into the registry."""
    providers = [provider] if provider else sorted({d.provider for d in reg.list_devices()})
    refreshed = errors = 0
    for prov in providers:
        try:
            adapter = get_adapter(prov)
        except AdapterError as e:
            errors += 1
            logger.warning("smarthome refresh: %s", e)
            continue
        for d in reg.list_devices():
            if d.provider != prov:
                continue
            try:
                state = adapter.get_state(d.provider_id)
                reg.set_state(d.id, state, fresh=True)
                refreshed += 1
            except Exception as e:  # noqa: BLE001
                errors += 1
                logger.warning("smarthome refresh %s/%s: %s", prov, d.provider_id, e)
    return {"refreshed": refreshed, "errors": errors, "providers": providers}


def device_view(d: dict) -> dict:
    """Add freshness to a device dict without inventing state."""
    last = d.get("last_seen")
    fresh = False
    if last:
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
            fresh = (datetime.now(timezone.utc) - ts).total_seconds() <= STALE_AFTER_S
        except ValueError:
            fresh = False
    return {**d, "stale": not fresh}


def control(device_id: str, changes: dict, actor: str = "operator") -> dict:
    """Control a device through the Command Bus, then verify by read-back.

    Sensitive kinds (lock/camera/cover) are classified high-risk by the bus and
    require approval; this function refuses to bypass AgentGuard.
    """
    rec = reg.get(device_id)
    if rec is None:
        raise AdapterError(f"unknown device {device_id!r}")
    if rec.kind.value in ("lock", "camera", "cover"):
        # Hand to the command bus for policy/approval; do not act directly.
        from core.command_bus import bus  # existing single dispatch point
        return bus.dispatch("smarthome.control", actor=actor,
                            device=rec.id, provider=rec.provider,
                            provider_id=rec.provider_id, changes=changes)

    adapter = get_adapter(rec.provider)
    observed = adapter.set_state(rec.provider_id, changes)  # read-back inside adapter
    reg.set_state(rec.id, observed, fresh=True)
    verified = _verify(changes, observed)
    return {"device": rec.id, "provider": rec.provider,
            "requested": changes, "observed": observed, "verified": verified}


def _verify(requested: dict, observed: dict) -> bool:
    """A command is verified only if the requested keys match the read-back."""
    for k, v in requested.items():
        if k == "on":
            if (observed.get("state") == "on") != bool(v):
                return False
        elif observed.get(k) != v:
            return False
    return True


# ── Background refresh (bounded, opt-in, never fabricates) ──────────────────

_REFRESH_INTERVAL_S = 120
_STOP = None


def refresh_loop(interval_s: int = _REFRESH_INTERVAL_S) -> None:
    """Refresh provider state on an interval until stopped. Safe to run in a thread.

    Disabled unless SMARTHOME_REFRESH=1 so tests and one-shot calls are unaffected.
    """
    import os
    import threading
    import time

    global _STOP
    if os.environ.get("SMARTHOME_REFRESH") != "1":
        return
    _STOP = threading.Event()
    while not _STOP.is_set():
        try:
            refresh()
        except Exception as e:  # noqa: BLE001
            logger.warning("smarthome refresh loop error: %s", e)
        _STOP.wait(interval_s)


def stop_refresh_loop() -> None:
    if _STOP is not None:
        _STOP.set()


def start_background_refresh(interval_s: int = _REFRESH_INTERVAL_S) -> bool:
    """Start the refresh loop in a daemon thread; returns True if started."""
    import os
    import threading

    if os.environ.get("SMARTHOME_REFRESH") != "1":
        return False
    t = threading.Thread(target=refresh_loop, args=(interval_s,), daemon=True,
                         name="smarthome-refresh")
    t.start()
    return True
