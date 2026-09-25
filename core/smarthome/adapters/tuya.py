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

    def _cloud(self):
        """Lazily build the cloud fallback adapter (for devices off this LAN)."""
        try:
            from core.smarthome.adapters.tuya_cloud import TuyaCloudAdapter
        except Exception:  # noqa: BLE001
            return None
        try:
            return TuyaCloudAdapter()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _is_local_ip(ip: str | None) -> bool:
        return bool(ip) and (ip.startswith("192.168.") or ip.startswith("10.")
                             or ip.startswith("172.")) and not ip.startswith("169.155")

    def _local_works(self, provider_id: str) -> bool:
        d = self._device(provider_id)
        return bool(d.get("local_key")) and self._is_local_ip(d.get("ip"))

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
        # Local-first when the device is on this LAN; otherwise cloud.
        if self._local_works(provider_id):
            try:
                return self._normalise(self._client(provider_id).status())
            except Exception as e:  # noqa: BLE001
                logger.warning("tuya local status failed for %s: %s", provider_id, e)
        cloud = self._cloud()
        if cloud is not None:
            return cloud.get_state(provider_id)
        raise AdapterError(f"Tuya device {provider_id} unreachable (no local path, no cloud session)")

    def set_state(self, provider_id: str, changes: dict) -> dict:
        if self._local_works(provider_id):
            try:
                dev = self._client(provider_id)
                dps: dict = {}
                if "on" in changes:
                    dps["1"] = bool(changes["on"])
                if "brightness" in changes:
                    dps["2"] = int(changes["brightness"])
                if not dps:
                    raise AdapterError("no controllable Tuya fields in request")
                if len(dps) > 1:
                    dev.set_multiple_values(dps)
                else:
                    dev.set_value(next(iter(dps)), next(iter(dps.values())))
                return self._normalise(dev.status())  # read-back
            except Exception as e:  # noqa: BLE001
                logger.warning("tuya local control failed for %s: %s", provider_id, e)
        cloud = self._cloud()
        if cloud is not None:
            return cloud.set_state(provider_id, changes)
        raise AdapterError(f"Tuya device {provider_id} not controllable (no local path, no cloud session)")
