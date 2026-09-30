"""Cisco Duo Push approval for the Kai Vault (roadmap 27S / §10/§13).

Implements the Duo Auth API "push" flow used to require human approval before a
high-risk vault operation: request an async push (``/auth/v2/auth``) and poll
``/auth/v2/auth_status`` until allow/deny/timeout. Requests are signed with the
documented scheme (HMAC-SHA1 over a canonical method/host/path/params string,
HTTP Basic ``ikey:signature``).

Configure via env: ``DUO_IKEY``, ``DUO_SKEY``, ``DUO_API_HOST`` (and optional
``DUO_USERNAME``). Without a tenant the feature stays disabled (additive).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from urllib.parse import quote

import requests


class DuoError(Exception):
    pass


def config_from_env() -> dict:
    return {
        "ikey": os.environ.get("DUO_IKEY", ""),
        "skey": os.environ.get("DUO_SKEY", ""),
        "host": os.environ.get("DUO_API_HOST", ""),
        "username": os.environ.get("DUO_USERNAME", ""),
    }


def is_configured(cfg: dict | None = None) -> bool:
    c = cfg or config_from_env()
    return bool(c.get("ikey") and c.get("skey") and c.get("host"))


def _canonical_params(params: dict) -> str:
    return "&".join(f"{quote(str(k), safe='')}={quote(str(params[k]), safe='')}"
                    for k in sorted(params))


def sign(ikey: str, skey: str, host: str, method: str, path: str,
         params: dict, date: str | None = None) -> tuple:
    """Return (authorization_header, date_header) for a Duo API request."""
    date = date or time.strftime("%a, %d %b %Y %H:%M:%S %z", time.gmtime())
    canonical = "\n".join([date, method.upper(), host, path, _canonical_params(params)])
    signature = hmac.new(skey.encode(), canonical.encode(), hashlib.sha1).hexdigest()
    basic = base64.b64encode(f"{ikey}:{signature}".encode()).decode()
    return f"Basic {basic}", date


class DuoClient:
    def __init__(self, ikey: str, skey: str, host: str, timeout: float = 10.0):
        self.ikey, self.skey, self.host, self.timeout = ikey, skey, host, timeout

    def _request(self, method: str, path: str, params: dict) -> dict:
        header, date = sign(self.ikey, self.skey, self.host, method, path, params)
        url = f"https://{self.host}{path}"
        headers = {"Authorization": header, "Date": date}
        try:
            if method.upper() == "POST":
                r = requests.post(url, data=params, headers=headers, timeout=self.timeout)
            else:
                r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise DuoError(f"duo request failed: {type(e).__name__}") from e
        try:
            body = r.json()
        except ValueError as e:
            raise DuoError(f"non-JSON response ({r.status_code})") from e
        if body.get("stat") != "OK":
            msg = body.get("message") or body.get("message_detail") or f"Duo error ({r.status_code})"
            raise DuoError(msg)
        return body.get("response", {})

    def push(self, username: str, device: str = "auto") -> str:
        resp = self._request("POST", "/auth/v2/auth", {
            "username": username, "factor": "push", "device": device, "async": "1"})
        txid = resp.get("txid")
        if not txid:
            raise DuoError("no txid returned")
        return txid

    def send_sms(self, username: str, device: str = "auto") -> dict:
        """Send a passcode by SMS (fallback when no push device is enrolled)."""
        return self._request("POST", "/auth/v2/auth", {
            "username": username, "factor": "sms", "device": device})

    def verify_passcode(self, username: str, passcode: str, device: str = "auto") -> dict:
        """Verify an SMS (or other) passcode."""
        return self._request("POST", "/auth/v2/auth", {
            "username": username, "factor": "passcode", "passcode": str(passcode),
            "device": device})

    def status(self, txid: str) -> str:
        resp = self._request("GET", "/auth/v2/auth_status", {"txid": txid})
        return resp.get("result", "unknown")

    def _push_device(self, username: str) -> str:
        """Resolve an explicit push-capable device id (Duo now rejects "auto")."""
        pre = self._request("POST", "/auth/v2/preauth", {"username": username})
        for d in (pre.get("devices") or []):
            if "push" in (d.get("capabilities") or []):
                return d["device"]
        return "auto"

    def approve(self, username: str, timeout: int = 60, interval: float = 2.0) -> dict:
        txid = self.push(username, device=self._push_device(username))
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                result = self.status(txid)
            except DuoError as e:      # transient (read timeout) — keep waiting
                last_err = str(e)
                time.sleep(interval)
                continue
            if result == "allow":
                return {"approved": True, "txid": txid, "result": result}
            if result == "deny":
                return {"approved": False, "txid": txid, "result": result}
            time.sleep(interval)
        return {"approved": False, "txid": txid, "result": "timeout",
                "error": last_err}
