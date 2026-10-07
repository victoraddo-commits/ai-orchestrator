"""Command Center WireGuard controller (runs on CT111).

The WG server is **CT105** (``Ohio``) on pve-A. It is not reachable
on the LAN from the CC, so management rides the existing key-based SSH chain::

    CT111 --ssh(kai_pve_usage)--> pve-B --ssh--> pve-A --pct exec--> CT105
          python3 /opt/kai-wg-agent/wg_agent.py <op> [args...]

No new listener is exposed on the WG host; the only write path is the agent's
own double gate (``WG_AGENT_ALLOW_WRITE=1`` + action token file). Read ops are
always allowed.

This module is a thin transport + enrichment layer:

* it builds the agent argv and parses the single JSON object the agent prints;
* it classifies each peer's state (``connected`` / ``idle`` / ``disconnected`` /
  ``paused``) from handshake age, and merges usage deltas from the snapshot log;
* it audit-logs every mutation with the operator identity.

The transport is injectable (``set_transport``) so tests never touch SSH.
Client private keys are never stored or re-emitted: the agent returns a client
config exactly once, in the ``add_peer`` response.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import subprocess
import time

DEFAULT_AGENT = "/opt/kai-wg-agent/wg_agent.py"
SSH_KEY = os.environ.get("WG_SSH_KEY", "/root/.ssh/kai_pve_usage")
PVE_B = os.environ.get("WG_PVE_B", "root@192.168.1.110")
PVE_A = os.environ.get("WG_PVE_A", "root@192.168.99.2")
WG_CTID = os.environ.get("WG_CTID", "105")
WG_AGENT_TOKEN_FILE = os.environ.get("WG_AGENT_TOKEN_FILE",
                                     "/etc/kai/wg_agent_token")
SSH_TIMEOUT = int(os.environ.get("WG_SSH_TIMEOUT", "30"))

# Handshake-age thresholds (seconds) for the state light.
CONNECTED_MAX_AGE = 180
IDLE_MAX_AGE = 900

READ_OPS = {"list", "usage", "server_export", "snapshot"}
WRITE_OPS = {"add_peer", "del_peer", "pause", "resume", "rename",
             "set_allowed", "restart"}


class WgServiceError(Exception):
    """Raised for any failure; carries an HTTP-ish status for the route layer."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _read_token() -> str:
    tok = os.environ.get("WG_AGENT_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(WG_AGENT_TOKEN_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def build_agent_command(op: str, args=(), *, write: bool = False,
                        agent: str = DEFAULT_AGENT, ctid: str = WG_CTID,
                        key: str = SSH_KEY, pve_b: str = PVE_B,
                        pve_a: str = PVE_A, token: str | None = None,
                        actor: str | None = None) -> list:
    """Return the ``ssh`` argv that runs one agent request on CT105."""
    if op not in READ_OPS and op not in WRITE_OPS:
        raise WgServiceError(f"unknown agent op: {op!r}", status=500)
    quoted_args = " ".join(shlex.quote(str(a)) for a in args)
    if write:
        if token is None:
            token = _read_token()
        env = ["env", "WG_AGENT_ALLOW_WRITE=1",
               f"WG_AGENT_TOKEN={shlex.quote(token)}"]
        if actor:
            env.append(f"WG_AGENT_BY={shlex.quote(actor)}")
        inner = f"pct exec {ctid} -- " + " ".join(env + ["python3", agent, op])
        if quoted_args:
            inner += f" {quoted_args}"
    else:
        inner = f"pct exec {ctid} -- python3 {shlex.quote(agent)} {op}"
        if quoted_args:
            inner += f" {quoted_args}"
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

    def call(self, op: str, args=(), *, write: bool = False,
             actor: str | None = None) -> dict:
        cmd = build_agent_command(op, args, write=write, actor=actor, **self._kw)
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
            raise WgServiceError(body.get("error", "agent error"),
                                 status=int(body.get("status", 400)))
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


def _call(op: str, args=(), *, write: bool = False,
          actor: str | None = None) -> dict:
    return _get_transport().call(op, args, write=write, actor=actor)


# ---------------------------------------------------------------------------
# QR rendering (controller-side fallback; agent also renders one)
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
# State classification + enrichment
# ---------------------------------------------------------------------------

def classify_state(latest_handshake: int, paused: bool, *,
                   now: int | None = None,
                   connected_max: int = CONNECTED_MAX_AGE,
                   idle_max: int = IDLE_MAX_AGE) -> str:
    """Per-peer state used for the status lights.

    ``paused`` always wins. Otherwise, handshake age (seconds):
    ``< connected_max`` -> connected; ``connected_max..idle_max`` -> idle;
    ``> idle_max`` or never (0) -> disconnected.
    """
    if paused:
        return "paused"
    if not latest_handshake:
        return "disconnected"
    now = int(time.time()) if now is None else int(now)
    age = now - int(latest_handshake)
    if age < 0:
        age = 0
    if age < connected_max:
        return "connected"
    if age <= idle_max:
        return "idle"
    return "disconnected"


def _handshake_age(latest_handshake: int, now: int) -> int | None:
    if not latest_handshake:
        return None
    age = now - int(latest_handshake)
    return age if age >= 0 else 0


def enrich_peers(data: dict, usage: dict | None = None,
                 now: int | None = None) -> dict:
    """Attach state lights + health + usage deltas to an agent ``list`` payload."""
    now = int(time.time()) if now is None else int(now)
    upairs = (usage or {}).get("peers", {})
    peers = []
    counts = {"connected": 0, "idle": 0, "disconnected": 0, "paused": 0}
    for p in data.get("peers", []):
        paused = bool(p.get("paused"))
        hs = p.get("latest_handshake", 0)
        state = classify_state(hs, paused, now=now)
        counts[state] = counts.get(state, 0) + 1
        u = upairs.get(p.get("pubkey"), {})
        merged = {
            **p,
            "state": state,
            "handshake_age_s": _handshake_age(hs, now),
            "health": {
                "endpoint": p.get("endpoint"),
                "latest_handshake": hs,
                "handshake_age_s": _handshake_age(hs, now),
            },
            "rx_delta_24h": u.get("rx_delta_24h", 0),
            "tx_delta_24h": u.get("tx_delta_24h", 0),
            "rx_delta_7d": u.get("rx_delta_7d", 0),
            "tx_delta_7d": u.get("tx_delta_7d", 0),
        }
        peers.append(merged)
    server = data.get("server", {})
    return {
        "server": server,
        "count": len(peers),
        "states": counts,
        "peers": peers,
        "generated_at": data.get("generated_at"),
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _audit(op: str, actor: str, *, pubkey: str = "", name: str = "",
           extra: dict | None = None) -> None:
    try:
        from core import audit_logger
        audit_logger.log_audit_event(
            event_type=f"wireguard.{op}",
            operator=actor or "operator",
            endpoint="/api/wg/peers",
            method="POST",
            status_code=200,
            details={"pubkey": pubkey, "name": name, **(extra or {})},
        )
    except Exception:  # noqa: BLE001 - audit must never break the action
        pass


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def list_peers() -> dict:
    """Enriched peer list (state lights + health + usage deltas)."""
    data = _call("list")
    try:
        usage = _call("usage", ["--window", "24h"])
    except WgServiceError:
        usage = {}
    return enrich_peers(data, usage)


def server_status() -> dict:
    """Server status card data."""
    data = _call("list")
    server = dict(data.get("server", {}))
    peers = data.get("peers", [])
    server["peer_count"] = len(peers)
    now = int(time.time())
    server["states"] = {"connected": 0, "idle": 0, "disconnected": 0,
                        "paused": 0}
    for p in peers:
        s = classify_state(p.get("latest_handshake", 0),
                           bool(p.get("paused")), now=now)
        server["states"][s] += 1
    return server


def list_peers_enriched() -> dict:
    return list_peers()


def usage(pubkey: str = "", window: str = "24h") -> dict:
    args = ["--window", window]
    if pubkey:
        args = ["--peer", pubkey, *args]
    return _call("usage", args)


def add_peer(name: str, *, actor: str = "operator", **opts) -> dict:
    if not name:
        raise WgServiceError("name is required", status=400)
    args = [name]
    mapping = {"ip": "--ip", "dns": "--dns", "allowed_ips": "--allowed-ips",
               "endpoint": "--endpoint", "mtu": "--mtu",
               "keepalive": "--keepalive", "mode": "--mode",
               "peer_lans": "--peer-lans", "note": "--note"}
    for key, flag in mapping.items():
        val = opts.get(key)
        if val is None or val == "":
            continue
        if isinstance(val, (list, tuple)):
            val = ",".join(str(v) for v in val)
        args += [flag, str(val)]
    data = _call("add_peer", args, write=True, actor=actor)
    # Prefer a controller-rendered QR (segno) but keep the agent's if present.
    if data.get("config") and not data.get("qr_png_base64"):
        data["qr_png_base64"] = qr_png_base64(data["config"])
    _audit("add", actor, pubkey=data.get("pubkey", ""), name=name)
    return data


def delete_peer(pubkey: str, actor: str = "operator") -> dict:
    data = _call("del_peer", [pubkey], write=True, actor=actor)
    _audit("delete", actor, pubkey=pubkey)
    return data


def pause_peer(pubkey: str, actor: str = "operator") -> dict:
    data = _call("pause", [pubkey], write=True, actor=actor)
    _audit("pause", actor, pubkey=pubkey)
    return data


def resume_peer(pubkey: str, actor: str = "operator") -> dict:
    data = _call("resume", [pubkey], write=True, actor=actor)
    _audit("resume", actor, pubkey=pubkey)
    return data


def rename_peer(pubkey: str, name: str = "", actor: str = "operator",
                **opts) -> dict:
    if not name:
        raise WgServiceError("name is required", status=400)
    data = _call("rename", [pubkey, name], write=True, actor=actor)
    _audit("rename", actor, pubkey=pubkey, name=name)
    return data


def set_allowed(pubkey: str, ips: str, actor: str = "operator") -> dict:
    if not ips:
        raise WgServiceError("ips is required", status=400)
    data = _call("set_allowed", [pubkey, ips], write=True, actor=actor)
    _audit("set_allowed", actor, pubkey=pubkey, extra={"allowed_ips": ips})
    return data


def restart_server(actor: str = "operator") -> dict:
    data = _call("restart", write=True, actor=actor)
    _audit("restart", actor)
    return data


def server_export() -> dict:
    return _call("server_export")


def peer_config(pubkey: str, fmt: str = "wg") -> dict:
    """Client keys are one-time: re-export is intentionally unavailable."""
    raise WgServiceError(
        "client key was shown once at creation and is not stored; "
        "delete and re-add the peer to issue a fresh config", status=410)


def peer_qr(pubkey: str, fmt: str = "wg") -> dict:
    try:
        cfg = peer_config(pubkey, fmt)
    except WgServiceError:
        raise
    return {"pubkey": pubkey, "type": fmt, "name": cfg.get("name", ""),
            "qr_png_base64": qr_png_base64(cfg["config"])}
