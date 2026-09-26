"""Tuya adapter — cloud control path.

Some Tuya account devices are NOT on our LAN (their local IPs are a different
site's WAN addresses), so local protocol is impossible. For those, `send_commands`
via the Smart Life cloud account is the only honest control path.

The cloud session is established once (device-sharing QR login) and its token +
terminal endpoint are stored in KAI's encrypted vault under `tuya_cloud`. This
adapter is used when a device has no local key reachable, and it never pretends
a device is local when it is not.
"""
from __future__ import annotations

import json
import logging

from core.smarthome.adapters.base import AdapterError, ProviderAdapter

logger = logging.getLogger(__name__)

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"


def _cloud_session() -> dict:
    try:
        from core.ai.credential_vault import retrieve_credential
        c = retrieve_credential("tuya_cloud") or {}
        if c.get("api_key"):
            return json.loads(c["api_key"])
    except Exception:  # noqa: BLE001
        pass
    return {}


class TuyaCloudAdapter(ProviderAdapter):
    name = "tuya_cloud"

    def __init__(self, session: dict | None = None):
        self._session = session if session is not None else _cloud_session()
        self._manager = None

    def _mgr(self):
        if self._manager is not None:
            return self._manager
        s = self._session
        if not s or not s.get("access_token"):
            raise AdapterError("Tuya cloud session not configured (tuya_cloud)")
        try:
            from tuya_sharing import Manager  # type: ignore
        except Exception:  # noqa: BLE001
            raise AdapterError("tuya_sharing is not installed")
        class _L:
            def token_updated_callback(self, i):
                pass

            def on_logout(self):
                pass

        listener = _L()
        self._manager = Manager(CLIENT_ID, s["uid"], s["terminal_id"],
                                s["endpoint"], s, listener)
        return self._manager

    def identify(self) -> list[dict]:
        mgr = self._mgr()
        mgr.update_device_cache()
        return [{"provider_id": d.id, "name": d.name}
                for d in mgr.device_map.values()]

    def get_state(self, provider_id: str) -> dict:
        mgr = self._mgr()
        mgr.update_device_cache()
        dev = mgr.device_map.get(provider_id)
        if dev is None:
            raise AdapterError(f"device {provider_id} not in Tuya cloud account")
        # We only surface what the account actually reports.
        return {"online": bool(getattr(dev, "online", False)),
                "product_name": getattr(dev, "product_name", None)}

    def set_state(self, provider_id: str, changes: dict) -> dict:
        mgr = self._mgr()
        cmds = []
        if "on" in changes:
            cmds.append({"code": "switch_1", "value": bool(changes["on"])})
        if not cmds:
            raise AdapterError("no controllable Tuya cloud fields in request")
        try:
            mgr.send_commands(provider_id, cmds)
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"Tuya cloud control failed: {e}")
        return self.get_state(provider_id)  # read-back

    def health(self) -> dict:
        """Probe the cloud session without inventing device state.

        A successful ``update_device_cache`` proves the shared session is
        live. A ``1010``/expiry/sign error means the QR-login session has
        lapsed and needs a re-scan — surfaced as an operator notice rather
        than silently showing cloud devices as merely "unavailable".
        """
        s = self._session
        if not s or not s.get("access_token"):
            return {"ok": False,
                    "detail": "no tuya_cloud session stored",
                    "notice": "Tuya cloud session missing — run the QR login"}
        try:
            mgr = self._mgr()
            mgr.update_device_cache()
            return {"ok": True, "detail": "cloud session live",
                    "devices": len(getattr(mgr, "device_map", {}) or {})}
        except AdapterError as e:
            return {"ok": False, "detail": str(e),
                    "notice": "Tuya cloud session error — re-scan may be needed"}
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            expired = ("1010" in msg or "expired" in low or "invalid" in low
                       or "sign" in low)
            return {"ok": False, "detail": msg[:200],
                    "notice": ("Tuya cloud session expired — re-scan the Smart "
                               "Life QR to restore cloud device control")
                              if expired else
                              "Tuya cloud unreachable — check network/session"}
