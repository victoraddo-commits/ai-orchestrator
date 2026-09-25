"""Home Assistant adapter — KAI drives HA (HA is a fabric, not the authority).

The long-lived token is read from KAI Vault, never from env or the frontend.
``session`` may be injected for tests; production passes ``requests.Session``.
State is returned verbatim from HA — fields HA does not report are absent, and
the caller renders them UNKNOWN.
"""
from __future__ import annotations

import logging

from core.smarthome.adapters.base import AdapterError, ProviderAdapter

logger = logging.getLogger(__name__)

_SERVICE_DOMAINS = {"light", "switch", "climate", "lock", "cover", "media_player"}


def _vault_token() -> str:
    try:
        from core import vault as _vault  # type: ignore
        for fn_name in ("get_secret", "read_secret", "get"):
            fn = getattr(_vault, fn_name, None)
            if fn:
                try:
                    return fn("homeassistant", "token") or ""
                except Exception:  # noqa: BLE001
                    continue
        return ""
    except Exception:  # noqa: BLE001
        return ""


class HomeAssistantAdapter(ProviderAdapter):
    name = "homeassistant"

    def __init__(self, base_url: str, token: str | None = None, session=None):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else _vault_token()
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def _headers(self) -> dict:
        if not self.token:
            raise AdapterError("Home Assistant token is not configured")
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _get(self, path: str):
        r = self.session.get(self.base_url + path, headers=self._headers(), timeout=10)
        if r.status_code != 200:
            raise AdapterError(f"HA GET {path} -> {r.status_code}")
        return r.json()

    def identify(self) -> list[dict]:
        out = []
        for s in self._get("/api/states"):
            eid = s.get("entity_id", "")
            dom = eid.split(".", 1)[0]
            if dom in ("light", "switch", "sensor", "climate", "lock", "cover",
                       "camera", "media_player", "binary_sensor", "button"):
                out.append({"provider_id": eid,
                            "name": s.get("attributes", {}).get("friendly_name", eid),
                            "kind": dom})
        return out

    def get_state(self, provider_id: str) -> dict:
        s = self._get(f"/api/states/{provider_id}")
        st = {"state": s.get("state")}
        for k, v in s.get("attributes", {}).items():
            if k in ("brightness", "color_temp", "temperature", "current_temperature",
                     "battery_level", "unit_of_measurement", "friendly_name"):
                st[k] = v
        return st

    def set_state(self, provider_id: str, changes: dict) -> dict:
        domain = provider_id.split(".", 1)[0]
        if domain not in _SERVICE_DOMAINS:
            raise AdapterError(f"HA cannot control domain {domain!r}")
        service = "turn_on" if changes.get("on") else "turn_off"
        body = {k: v for k, v in changes.items() if k != "on"}
        body["entity_id"] = provider_id
        r = self.session.post(f"{self.base_url}/api/services/{domain}/{service}",
                              headers=self._headers(), json=body, timeout=10)
        if r.status_code >= 400:
            raise AdapterError(f"HA control {provider_id} -> {r.status_code}")
        return self.get_state(provider_id)  # read-back
