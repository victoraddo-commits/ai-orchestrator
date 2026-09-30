"""HTTP client for the CT110 browser service (used by the CT111 orchestrator).

Transport is injectable so the orchestrator's fast tests never touch the
network; in production it uses ``httpx``. The client is intentionally thin — all
governance (mission linkage, provider gate, event bus, audit) lives in
:mod:`core.browser.integration`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_URL = os.environ.get("KAI_BROWSER_URL", "http://192.168.1.119:8090")


class BrowserServiceError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"browser service error {status}: {message}")
        self.status = status
        self.message = message


def _urllib_transport(base_url: str, token: Optional[str]):
    def transport(method: str, path: str, body: Optional[dict]) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                detail = json.loads(payload or b"{}").get("detail", payload.decode(errors="replace"))
            except Exception:
                detail = payload.decode(errors="replace")
            return exc.code, {"detail": detail}

    return transport


class BrowserOperatorClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        transport=None,
        timeout: int = 30,
    ):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("KAI_BROWSER_TOKEN")
        self._transport = transport or _urllib_transport(self.base_url, self.token)
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        status, payload = self._transport(method, path, body)
        if status >= 400:
            raise BrowserServiceError(status, str(payload.get("detail", payload)))
        return payload

    # -- health / profiles -----------------------------------------------------
    def health(self) -> dict:
        return self._call("GET", "/health")

    def create_profile(self, identity_id: str, provider_id: str) -> dict:
        return self._call("POST", "/profiles", {"identity_id": identity_id, "provider_id": provider_id})

    def set_provider_domain(self, provider_id: str, official_domain: str) -> dict:
        return self._call("POST", "/providers/domain",
                          {"provider_id": provider_id, "official_domain": official_domain})

    def list_profiles(self) -> dict:
        return self._call("GET", "/profiles")

    # -- sessions --------------------------------------------------------------
    def open_session(self, identity_id: str, provider_id: str, *,
                     mission_id: Optional[str] = None, headless: Optional[bool] = None) -> dict:
        return self._call("POST", "/sessions", {
            "identity_id": identity_id, "provider_id": provider_id,
            "mission_id": mission_id, "headless": headless})

    def get_session(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}")

    def perform(self, session_id: str, op: str, params: Optional[dict] = None) -> dict:
        return self._call("POST", f"/sessions/{session_id}/op", {"op": op, "params": params or {}})

    def pause_for_human(self, session_id: str, **kwargs) -> dict:
        return self._call("POST", f"/sessions/{session_id}/pause", kwargs)

    def resume(self, session_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/resume", {})

    def end_session(self, session_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/end", {})

    def get_takeover(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}/takeover")

    def list_takeovers(self) -> dict:
        return self._call("GET", "/takeovers")
