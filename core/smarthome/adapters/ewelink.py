"""eWeLink / Sonoff adapter — cloud control (v2 CoolKit API).

The eWeLink devices on this LAN (e.g. 192.168.1.13, Sonoff PSF-B01-GL) are
controlled through the eWeLink cloud using the account session, exactly as
verified: POST /v2/device/thing/status with params:{switch:"on"|"off"}; reads
via GET /v2/device/thing. Credentials + session live in KAI's encrypted vault
(provider `ewelink`). Nothing is fabricated: unknown fields are omitted, and a
failed command raises AdapterError.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from core.smarthome.adapters.base import AdapterError, ProviderAdapter

logger = logging.getLogger(__name__)

REGIONS = {"cn": "https://cn-apia.coolkit.cn", "as": "https://as-apia.coolkit.cc",
           "us": "https://us-apia.coolkit.cc", "eu": "https://eu-apia.coolkit.cc"}


def _session() -> dict:
    """Return the stored eWeLink session {at, appid, region} or {}."""
    try:
        from core.ai.credential_vault import retrieve_credential
        c = retrieve_credential("ewelink_session") or {}
        if c.get("api_key"):
            return json.loads(c["api_key"])
    except Exception:  # noqa: BLE001
        pass
    return {}


class EWeLinkAdapter(ProviderAdapter):
    name = "ewelink"

    def __init__(self, session: dict | None = None):
        self._s = session if session is not None else _session()

    @property
    def _host(self) -> str:
        return REGIONS.get(self._s.get("region", "eu"), REGIONS["eu"])

    @property
    def _headers(self) -> dict:
        if not self._s.get("at"):
            raise AdapterError("eWeLink session not configured (ewelink_session)")
        return {"Authorization": "Bearer " + self._s["at"],
                "X-CK-Appid": self._s["appid"],
                "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._host + path, data=data,
                                     headers=self._headers, method=method)
        try:
            return json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError as e:
            raise AdapterError(f"eWeLink HTTP {e.code}: {e.read()[:120].decode('utf-8','replace')}")
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"eWeLink request failed: {type(e).__name__}")

    def _things(self) -> list[dict]:
        r = self._req("GET", "/v2/device/thing?num=500&page=1")
        return (r.get("data") or {}).get("thingList") or []

    def identify(self) -> list[dict]:
        out = []
        for t in self._things():
            it = t.get("itemData") or {}
            if it.get("deviceid"):
                out.append({"provider_id": it["deviceid"], "name": it.get("name", it["deviceid"])})
        return out

    def get_state(self, provider_id: str) -> dict:
        for t in self._things():
            it = t.get("itemData") or {}
            if it.get("deviceid") == provider_id:
                params = it.get("params") or {}
                st: dict = {"online": bool(it.get("online"))}
                if "switch" in params:
                    st["state"] = params["switch"]  # "on"/"off"
                for k in ("power", "voltage", "current", "rssi"):
                    if k in params:
                        st[k] = params[k]
                return st
        raise AdapterError(f"eWeLink device {provider_id} not found in account")

    def set_state(self, provider_id: str, changes: dict) -> dict:
        if "on" not in changes:
            raise AdapterError("no controllable eWeLink fields in request")
        params = {"switch": "on" if changes["on"] else "off"}
        r = self._req("POST", "/v2/device/thing/status",
                      {"type": 1, "id": provider_id, "params": params})
        if r.get("error") not in (0, None):
            raise AdapterError(f"eWeLink control failed: {r.get('msg')}")
        return self.get_state(provider_id)  # read-back
