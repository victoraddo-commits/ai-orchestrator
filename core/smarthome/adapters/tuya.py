"""Tuya adapter — local control (protocol 3.3/3.4).

The Tuya devices seen on this LAN (e.g. 192.168.1.16, MAC 50:8A:06...) speak an
**encrypted** local protocol; a per-device ``local_key`` is required. That key is
obtained once from the Smart Life / Tuya cloud account (or the device's pairing
data) and then stored in KAI's encrypted credential vault under provider key
``tuya:<gwId>``. Until a key exists the adapter reports ``not configured`` — it
never fabricates state and never guesses a key.

Local control uses ``tinytuya`` when installed (it implements the exact framing);
without it, the adapter still performs identity discovery and raises a typed
error on control so the UI shows "not configured" rather than fake data.
"""
from __future__ import annotations

import logging

from core.smarthome.adapters.base import AdapterError, ProviderAdapter

logger = logging.getLogger(__name__)

_KEY_PREFIX = "tuya:"


def vault_local_key(gw_id: str) -> str:
    """Return the stored local key for a Tuya device, or '' when absent."""
    if not gw_id:
        return ""
    try:
        from core.ai.credential_vault import retrieve_api_key
        return retrieve_api_key(_KEY_PREFIX + gw_id) or ""
    except Exception:  # noqa: BLE001
        return ""


class TuyaAdapter(ProviderAdapter):
    name = "tuya"

    def __init__(self, devices: dict | None = None):
        # devices: {gw_id: {"ip": str, "local_key": str, "version": "3.3"}}
        self._devices = devices or {}

    def _device(self, provider_id: str) -> dict:
        d = self._devices.get(provider_id)
        if not d:
            return {"ip": provider_id, "local_key": vault_local_key(provider_id),
                    "version": "3.3"}
        d = dict(d)
        d.setdefault("local_key", vault_local_key(provider_id))
        return d

    def _client(self, provider_id: str):
        d = self._device(provider_id)
        if not d.get("local_key"):
            raise AdapterError(
                f"Tuya local key for {provider_id} is not configured "
                "(store it in the vault as 'tuya:<gwId>')")
        try:
            import tinytuya  # type: ignore
        except Exception:  # noqa: BLE001
            raise AdapterError("tinytuya is not installed on the runner")
        dev = tinytuya.Device(d.get("dev_id", provider_id), d["ip"], d["local_key"])
        dev.set_version(float(d.get("version", "3.3")))
        return dev

    def identify(self) -> list[dict]:
        out = []
        for gw in self._devices:
            out.append({"provider_id": gw, "name": self._devices[gw].get("name", gw),
                        "kind": self._devices[gw].get("kind", "switch")})
        return out

    @staticmethod
    def _normalise(status: dict) -> dict:
        """Map Tuya DPs to KAI state without inventing fields."""
        dps = (status or {}).get("dps", {})
        state: dict = {}
        if "1" in dps:  # standard switch DP
            state["state"] = "on" if dps["1"] else "off"
        if "2" in dps:
            state["brightness"] = dps["2"]
        if "3" in dps:
            state["color_temp"] = dps["3"]
        return state

    def get_state(self, provider_id: str) -> dict:
        dev = self._client(provider_id)
        try:
            st = dev.status()
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"Tuya status failed for {provider_id}: {e}")
        if isinstance(st, dict) and st.get("Error"):
            raise AdapterError(st.get("Error"))
        return self._normalise(st)

    def set_state(self, provider_id: str, changes: dict) -> dict:
        dev = self._client(provider_id)
        dps: dict = {}
        if "on" in changes:
            dps["1"] = bool(changes["on"])
        if "brightness" in changes:
            dps["2"] = int(changes["brightness"])
        if not dps:
            raise AdapterError("no controllable Tuya fields in request")
        try:
            dev.set_multiple_values(dps) if len(dps) > 1 else dev.set_value(
                next(iter(dps)), next(iter(dps.values())))
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"Tuya control failed for {provider_id}: {e}")
        return self.get_state(provider_id)  # read-back
