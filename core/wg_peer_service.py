"""Command Center WireGuard peer controller (runs on CT111).

The Command Center must not reach the WireGuard host directly: CT102
(``wireguard``, ``10.6.0.1/24``) is not reachable over the LAN from the CC
(verified: ARP/TCP to ``192.168.1.182`` fails from PVE-A, PVE-B and CT111;
it is only reachable from PVE-A via ``pct exec``). We therefore manage it over
the *existing* key-based SSH chain::

    CT111 --ssh(kai_pve_usage)--> PVE-B --ssh--> PVE-A --pct exec--> CT102

This keeps the only management path on an already-trusted channel: no new
listener on the WireGuard host, no new firewall rules, no extra attack surface.

The heavy lifting lives in ``core.wg_agent`` deployed on CT102. This module is
a thin, injectable transport plus QR rendering. The transport is injected in
tests so no SSH is attempted.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import subprocess

DEFAULT_AGENT = "/opt/kai-wg-agent/wg_agent.py"
SSH_KEY = os.environ.get("WG_SSH_KEY", "/root/.ssh/kai_pve_usage")
PVE_B = os.environ.get("WG_PVE_B", "root@192.168.1.110")
PVE_A = os.environ.get("WG_PVE_A", "root@100.83.4.27")
WG_CTID = os.environ.get("WG_CTID", "102")
SSH_TIMEOUT = int(os.environ.get("WG_SSH_TIMEOUT", "30"))


class WgServiceError(Exception):
    """Raised for any failure; carries an HTTP-ish status for the route layer."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def build_agent_command(payload: dict, *, agent: str = DEFAULT_AGENT,
                        ctid: str = WG_CTID, key: str = SSH_KEY,
                        pve_b: str = PVE_B, pve_a: str = PVE_A) -> list:
    """Return the argv for ``ssh`` that runs one agent request on CT102."""
    b64 = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).decode()
    inner = f"pct exec {ctid} -- python3 {agent} {b64}"
    remote = (f"ssh -o BatchMode=yes -o StrictHostKeyChecking=no "
              f"-o ConnectTimeout=8 -o LogLevel=ERROR {pve_a} "
              f"{shlex.quote(inner)}")
    return ["ssh", "-i", key, "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8",
            "-o", "LogLevel=ERROR", pve_b, remote]


def _parse_agent_stdout(stdout: str) -> dict:
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise WgServiceError("agent returned no JSON", status=502)


class SshTransport:
    """Runs agent requests through the SSH chain. Only used in production."""

    def __init__(self, **kw):
        self._kw = kw

    def call(self, payload: dict) -> dict:
        cmd = build_agent_command(payload, **self._kw)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=SSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise WgServiceError("WireGuard agent timed out", status=504)
        except OSError as exc:
            raise WgServiceError(f"ssh failed: {exc}", status=502)
        if proc.returncode != 0 and not (proc.stdout or "").strip().startswith("{"):
            raise WgServiceError(
                f"agent unreachable (rc={proc.returncode}): "
                f"{(proc.stderr or '').strip()[:200]}", status=502)
        body = _parse_agent_stdout(proc.stdout)
        if not body.get("ok"):
            raise WgServiceError(body.get("error", "agent error"), status=400)
        return body.get("data", {})


_transport = None
_transport_override = None


def _get_transport():
    global _transport
    if _transport_override is not None:
        return _transport_override
    if _transport is None:
        _transport = SshTransport()
    return _transport


def set_transport(transport):
    """Test/deploy hook to inject a transport."""
    global _transport_override
    _transport_override = transport


def _call(payload: dict) -> dict:
    return _get_transport().call(payload)


# ---------------------------------------------------------------------------
# QR rendering
# ---------------------------------------------------------------------------

def qr_png_base64(text: str, scale: int = 4) -> str:
    """Render ``text`` as a PNG QR and return base64. segno preferred."""
    buf = io.BytesIO()
    try:
        import segno
        segno.make(text, error="m").save(buf, kind="png", scale=scale, border=2)
    except ImportError:
        import qrcode
        qrcode.make(text).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def list_peers() -> dict:
    return _call({"op": "list"})


def add_peer(name: str, **opts) -> dict:
    data = _call({"op": "add", "name": name, **opts})
    data["qr_png_base64"] = qr_png_base64(data["config"])
    return data


def pause_peer(pubkey: str) -> dict:
    return _call({"op": "pause", "pubkey": pubkey})


def resume_peer(pubkey: str) -> dict:
    return _call({"op": "resume", "pubkey": pubkey})


def delete_peer(pubkey: str) -> dict:
    return _call({"op": "delete", "pubkey": pubkey})


def rename_peer(pubkey: str, **opts) -> dict:
    """Update a managed peer's metadata (name/DNS/keepalive/MTU/allowed IPs)."""
    return _call({"op": "rename", "pubkey": pubkey, **opts})


def peer_config(pubkey: str, fmt: str = "wg") -> dict:
    return _call({"op": "config", "pubkey": pubkey, "type": fmt})


def peer_qr(pubkey: str, fmt: str = "wg") -> dict:
    cfg = peer_config(pubkey, fmt)
    return {"pubkey": pubkey, "type": fmt, "name": cfg.get("name", ""),
            "qr_png_base64": qr_png_base64(cfg["config"])}
