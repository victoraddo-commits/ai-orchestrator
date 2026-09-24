"""Tailscale node discovery via SSH on each configured Proxmox node.

Discovers: tailscale status --json, ip route, ip addr.
Classifies each peer as DIRECT_PEER / SUBNET_ROUTER / EXIT_NODE / ORDINARY_CLIENT.
"""

import json
import subprocess
import os
from datetime import datetime, timezone
from typing import Optional


# -------------------------------------------------------------------
# Configuration — read from environment (same pattern as proxmox_monitor.py)
# -------------------------------------------------------------------

TAILSCALE_NODES = [
    {
        "name": "pve",
        "host": os.environ.get("PROXMOX_HOST", "192.168.99.2"),
        "ssh_user": "root",
        "ssh_key": os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa"),
    },
    {
        "name": "pve-b",
        "host": os.environ.get("KAI_NETWORK_PVE_B_HOST",
                              os.environ.get("PROXMOX_B_HOST", "192.168.1.110")),
        "ssh_user": "root",
        "ssh_key": os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa"),
    },
]


def _default_ssh_key() -> str:
    for cand in (os.environ.get("KAI_DISCOVERY_SSH_KEY"),
                 os.environ.get("PROXMOX_SSH_KEY"),
                 "/root/.ssh/kai_pve_usage",
                 "/root/.ssh/id_rsa"):
        if cand and os.path.exists(cand):
            return cand
    return os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa")


def _ts_ips(obj: dict) -> list[str]:
    """Tailscale status uses ``TailscaleIPs``; accept the legacy ``TailnetIPs``.

    Older discovery fixtures/tests used ``TailnetIPs`` — read both so neither
    the live daemon nor the fixtures regress.
    """
    return list(obj.get("TailscaleIPs") or obj.get("TailnetIPs") or [])


def _advertised_routes(obj: dict) -> list[str]:
    """Route CIDRs a node advertises.

    ``AdvertiseRoutes`` is present on peers; for Self the daemon reports them
    under ``PrimaryRoutes``/``AllowedIPs``. Prefer the explicit advertise list.
    """
    routes = obj.get("AdvertiseRoutes")
    if routes:
        return list(routes)
    primary = obj.get("PrimaryRoutes")
    if primary:
        return list(primary)
    return []


# -------------------------------------------------------------------
# SSH helpers
# -------------------------------------------------------------------

def _ssh(node: dict, cmd: str) -> tuple[str, str, int]:
    """Run cmd via SSH on node. Returns (stdout, stderr, returncode)."""
    key = node.get("ssh_key") or ""
    if not key or not os.path.exists(key):
        key = _default_ssh_key()
    full_cmd = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=6",
        f"{node['ssh_user']}@{node['host']}",
        cmd,
    ]
    try:
        r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


# -------------------------------------------------------------------
# Classification
# -------------------------------------------------------------------

def _classify_node(peer: dict) -> str:
    """Classify a Tailscale peer by role."""
    if peer.get("exitNode", False):
        return "EXIT_NODE"
    if peer.get("AdvertiseRoutes"):
        return "SUBNET_ROUTER"
    if peer.get("Direct", False):
        return "DIRECT_PEER"
    return "ORDINARY_CLIENT"


def _parse_status_json(data: dict) -> tuple[dict, list]:
    """Parse tailscale status --json output. Returns (peers_dict, subnet_routes_list)."""
    peers = {}
    routes = []

    def add_peer(key: str, hostname: str, info: dict) -> str:
        """Insert a peer, de-duplicating colliding hostnames by tailnet IP."""
        if key in peers:
            ip = info.get("tailscale_ip") or "?"
            key = f"{key} [{ip}]"
        peers[key] = info
        return key

    self_node = data.get("Self", {})
    self_name = self_node.get("HostName", "self")
    self_ips = _ts_ips(self_node)
    self_key = add_peer(self_name, self_name, {
        "hostname": self_node.get("HostName", ""),
        "dns_name": self_node.get("DNSName", ""),
        "tailscale_ip": self_ips[0] if self_ips else "",
        "advertise_routes": _advertised_routes(self_node),
        "role": _classify_node(self_node),
        "online": True,
        "direct": True,
        "latency_ms": None,
    })
    for subnet in _advertised_routes(self_node):
        routes.append({
            "subnet": subnet,
            "advertiser": self_key,
            "accepted": self_node.get("CapMap", {}).get("act", [True])[0]
            if self_node.get("CapMap", {}).get("act") else True,
        })

    for name, peer in (data.get("Peer") or {}).items():
        role = _classify_node(peer)
        hostname = peer.get("HostName", name)
        ips = _ts_ips(peer)
        peer_key = add_peer(hostname, hostname, {
            "hostname": peer.get("HostName", ""),
            "dns_name": peer.get("DNSName", ""),
            "tailscale_ip": ips[0] if ips else "",
            "advertise_routes": _advertised_routes(peer),
            "role": role,
            "online": peer.get("Online", False),
            "direct": peer.get("Direct", False),
            "latency_ms": (peer.get("Latency") or {}).get("PingMs"),
            "last_seen": peer.get("LastSeen"),
        })
        for subnet in _advertised_routes(peer):
            routes.append({
                "subnet": subnet,
                "advertiser": peer_key,
                "accepted": peer.get("CapMap", {}).get("act", [True])[0]
                if peer.get("CapMap", {}).get("act") else True,
            })

    return peers, routes


# -------------------------------------------------------------------
# Main discovery
# -------------------------------------------------------------------

def discover_tailscale_on_node(node: dict) -> dict:
    """Run all discovery commands on one node via SSH. Returns parsed results."""
    result = {
        "node": node["name"],
        "host": node["host"],
        "reachable": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "peers": {},
        "subnet_routes": [],
        "error": None,
    }

    stdout, stderr, rc = _ssh(node, "tailscale status --json")
    if rc != 0:
        result["error"] = stderr.strip() or f"exit {rc}"
        return result

    result["reachable"] = True
    try:
        data = json.loads(stdout)
        result["peers"], result["subnet_routes"] = _parse_status_json(data)
    except json.JSONDecodeError as e:
        result["error"] = f"json parse error: {e}"
        return result

    # Collected for future use; not currently parsed but may be needed for
    # route-audit or policy debugging in a later phase.
    stdout, _, rc = _ssh(node, "ip route show table all")
    if rc == 0:
        result["routing_table"] = stdout

    return result


def discover_all_nodes() -> dict:
    """Discover Tailscale state on all configured nodes."""
    results = {}
    for node in TAILSCALE_NODES:
        results[node["name"]] = discover_tailscale_on_node(node)
    return results
