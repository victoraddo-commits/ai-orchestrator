"""A/B/C full-mesh WireGuard (``wg-kai``) status for the Command Center.

The mesh rides the tailnet (endpoints are Tailscale IPs) on its own subnet
``10.10.10.0/24`` / UDP 51900. The Command Center runs on CT111 (Proxmox B),
which cannot read the host's ``wg-kai`` directly; it therefore reads live
status over the *existing* key-based SSH chain::

    CT111 --ssh(kai_pve_usage)--> PVE-B --ssh--> {PVE-A, PVE-C}

Read-only. The interface private key is never parsed into a response and public
keys are masked. The static topology below is the source of truth for the
expected peer count; live ``wg show wg-kai dump`` output is merged in when a
node is reachable (an unreachable node is reported as such, never fabricated).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime, timezone

SSH_KEY = os.environ.get("WG_SSH_KEY", "/root/.ssh/kai_pve_usage")
PVE_B = os.environ.get("WG_PVE_B", "root@192.168.1.110")
PVE_A = os.environ.get("WG_PVE_A", "root@100.83.4.27")
PVE_C = os.environ.get("WG_PVE_C", "root@100.116.165.100")
IFACE = os.environ.get("WG_MESH_IFACE", "wg-kai")
SUBNET = os.environ.get("WG_MESH_SUBNET", "10.10.10.0/24")
PORT = int(os.environ.get("WG_MESH_PORT", "51900"))
SSH_TIMEOUT = int(os.environ.get("WG_MESH_SSH_TIMEOUT", "30"))
HANDSHAKE_FRESH_S = int(os.environ.get("WG_MESH_HANDSHAKE_FRESH_S", "180"))

# Static topology — matches docs/networking/ab_c_wireguard_mesh.md.
NODES = (
    {"name": "proxmox-a", "role": "A", "mesh_ip": "10.10.10.1",
     "tailnet_ip": "100.83.4.27", "target": PVE_A, "via": "PVE-B"},
    {"name": "proxmox-b", "role": "B", "mesh_ip": "10.10.10.2",
     "tailnet_ip": "100.122.38.118", "target": PVE_B, "via": None},
    {"name": "proxmox-c", "role": "C", "mesh_ip": "10.10.10.3",
     "tailnet_ip": "100.116.165.100", "target": PVE_C, "via": "PVE-B"},
)


class WgMeshError(Exception):
    """Any failure to gather live mesh status; carries an HTTP-ish status."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def build_command(node: dict, *, key: str = SSH_KEY, iface: str = IFACE,
                  timeout: int = 8) -> list:
    """Return the argv for ``ssh`` that runs ``wg show <iface> dump`` on node.

    Nodes marked ``via`` are reached by an inner ssh from PVE-B; the mesh host
    itself (PVE-B) is read directly.
    """
    base = ["ssh", "-i", key, "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", f"ConnectTimeout={timeout}", "-o", "LogLevel=ERROR"]
    inner = f"wg show {iface} dump"
    if node.get("via"):
        remote = (f"ssh -o BatchMode=yes -o StrictHostKeyChecking=no "
                  f"-o ConnectTimeout={timeout} -o LogLevel=ERROR "
                  f"{node['target']} {shlex.quote(inner)}")
        return base + [PVE_B, remote]
    return base + [node["target"], inner]


def mask_key(key: str, keep: int = 8) -> str:
    """Short, non-reversible display form of a WireGuard public key."""
    key = str(key or "")
    return key if len(key) <= keep else key[:keep] + "\u2026"


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def parse_dump(text: str) -> dict:
    """Parse ``wg show <iface> dump``.

    Field order (tab separated): interface line ``priv pub port fwmark`` then
    one line per peer ``pub psk endpoint allowed_ips latest_handshake rx tx
    keepalive``. The interface private key (field 0) is never read.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        raise WgMeshError("empty wg dump", status=502)
    header = lines[0].split("\t")
    if len(header) < 4:
        raise WgMeshError("malformed wg dump header", status=502)
    pub, port = header[1], header[2]
    now = _now_epoch()
    peers = []
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < 8:
            continue
        try:
            handshake = int(f[4] or 0)
        except ValueError:
            handshake = 0
        try:
            rx = int(f[5] or 0)
        except ValueError:
            rx = 0
        try:
            tx = int(f[6] or 0)
        except ValueError:
            tx = 0
        try:
            keepalive = int(f[7] or 0)
        except ValueError:
            keepalive = 0
        age = max(0, now - handshake) if handshake else None
        peers.append({
            "public_key": mask_key(f[0]),
            "endpoint": f[2] or "",
            "allowed_ips": [a for a in (f[3] or "").split(",") if a],
            "latest_handshake": handshake,
            "handshake_age_s": age,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "keepalive": keepalive,
            "up": bool(handshake) and age is not None and age < HANDSHAKE_FRESH_S,
        })
    listen_port = int(port) if str(port).isdigit() else port
    return {"public_key": mask_key(pub), "listen_port": listen_port,
            "peers": peers}


def _run(cmd: list, timeout: int = SSH_TIMEOUT) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise WgMeshError("mesh node timed out", status=504)
    except OSError as exc:
        raise WgMeshError(f"ssh failed: {exc}", status=502)
    if proc.returncode != 0:
        raise WgMeshError((proc.stderr or "ssh failed").strip()[:200],
                          status=502)
    return proc.stdout


_transport_override = None


def set_transport(fn) -> None:
    """Test hook: replace the live runner with ``fn(node) -> dump text``."""
    global _transport_override
    _transport_override = fn


def _fetch(node: dict) -> dict:
    if _transport_override is not None:
        return parse_dump(_transport_override(node))
    return parse_dump(_run(build_command(node)))


def mesh_status() -> dict:
    """Return the full A/B/C mesh snapshot, one record per node."""
    generated_at = datetime.now(timezone.utc).isoformat()
    expected = len(NODES) - 1
    nodes_out = []
    for node in NODES:
        rec = {
            "name": node["name"], "role": node["role"],
            "mesh_ip": node["mesh_ip"], "tailnet_ip": node["tailnet_ip"],
            "reachable": False, "error": None, "public_key": None,
            "listen_port": None, "peers": [], "peer_count": 0,
            "expected_peers": expected, "status": "unreachable",
        }
        try:
            data = _fetch(node)
            rec.update(reachable=True, public_key=data["public_key"],
                       listen_port=data["listen_port"], peers=data["peers"],
                       peer_count=len(data["peers"]))
            all_up = (rec["peer_count"] == expected
                      and all(p["up"] for p in data["peers"]))
            rec["status"] = "up" if all_up else (
                "degraded" if rec["peer_count"] else "no_peers")
        except Exception as exc:  # noqa: BLE001 - report, never 500 the panel
            rec["error"] = str(exc)
        nodes_out.append(rec)
    return {
        "schema": "wg-mesh/1",
        "generated_at": generated_at,
        "interface": IFACE,
        "subnet": SUBNET,
        "port": PORT,
        "transport": "tailscale underlay (WireGuard endpoints are tailnet IPs)",
        "handshake_fresh_s": HANDSHAKE_FRESH_S,
        "expected_peers_per_node": expected,
        "mesh_ok": all(n["status"] == "up" for n in nodes_out),
        "nodes": nodes_out,
    }
