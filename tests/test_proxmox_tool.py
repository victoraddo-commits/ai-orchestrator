"""Tests for the low-level Proxmox API client.

Regression guard for the 2026-09-20 false-negative: a non-JSON error body
(401/403/500/empty) made r.json() raise and api_request() returned
{"error": "Expecting value: ..."}, which the health analyzer rendered as a
critical "node unreachable" alert. The client must report the real HTTP
shape instead.
"""

from unittest.mock import patch

import tools.proxmox as proxmox


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def _call():
    return proxmox.api_request("/nodes/pve/status", host="h", token_id="t", token_secret="s")


def test_401_returns_auth_failed_with_http_status():
    with patch("tools.proxmox.requests.get", return_value=_Resp(401, text="authentication failure")):
        out = _call()

    assert out["error"] == "auth_failed"
    assert out["http"] == 401


def test_403_returns_auth_failed():
    with patch("tools.proxmox.requests.get", return_value=_Resp(403, text="permission denied")):
        out = _call()

    assert out["error"] == "auth_failed"
    assert out["http"] == 403


def test_empty_200_body_returns_invalid_json_not_a_crash():
    with patch("tools.proxmox.requests.get", return_value=_Resp(200, payload=None, text="")):
        out = _call()

    assert out["error"] == "invalid_json"
    assert out["http"] == 200


def test_500_returns_http_error():
    with patch("tools.proxmox.requests.get", return_value=_Resp(500, text="server error")):
        out = _call()

    assert out["error"] == "http_error"
    assert out["http"] == 500


def test_transport_exception_returns_unreachable():
    with patch("tools.proxmox.requests.get", side_effect=Exception("connection refused")):
        out = _call()

    assert out["error"] == "unreachable"
    assert "connection refused" in out["detail"]


def test_success_returns_parsed_payload():
    with patch("tools.proxmox.requests.get", return_value=_Resp(200, payload={"data": {"cpu": 0.1}})):
        out = _call()

    assert out["data"]["cpu"] == 0.1
    assert "error" not in out
