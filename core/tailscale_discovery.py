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
        "host": os.environ.get("PROXMOX_B_HOST", "192.168.1.109"),
        "ssh_user": "root",
        "ssh_key": os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa"),
    },
]


# -------------------------------------------------------------------
# SSH helpers
# -------------------------------------------------------------------

def _ssh(node: dict, cmd: str) -> tuple[str, str, int]:
    """Run cmd via SSH on node. Returns (stdout, stderr, returncode)."""
    key = node.get("ssh_key", "/root/.ssh/id_rsa")
    full_cmd = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
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
    return "ORDINARY_CLIENT"


def _parse_status_json(data: dict) -> tuple[dict, list]:
    """Parse tailscale status --json output. Returns (peers_dict, subnet_routes_list)."""
    peers = {}
    routes = []

    self_node = data.get("Self", {})
    self_name = self_node.get("HostName", "self")
    peers[self_name] = {
        "hostname": self_node.get("HostName", ""),
        "dns_name": self_node.get("DNSName", ""),
        "tailscale_ip": (self_node.get("TailnetIPs") or [""])[0],
        "advertise_routes": self_node.get("AdvertiseRoutes", []),
        "role": _classify_node(self_node),
        "online": True,
        "direct": True,
        "latency_ms": None,
    }
    for subnet in self_node.get("AdvertiseRoutes", []):
        routes.append({
            "subnet": subnet,
            "advertiser": self_name,
            "accepted": True,
        })

    for name, peer in (data.get("Peer") or {}).items():
        role = _classify_node(peer)
        peer_key = peer.get("HostName", name)
        peers[peer_key] = {
            "hostname": peer.get("HostName", ""),
            "dns_name": peer.get("DNSName", ""),
            "tailscale_ip": (peer.get("TailnetIPs") or [""])[0],
            "advertise_routes": peer.get("AdvertiseRoutes", []),
            "role": role,
            "online": peer.get("Online", False),
            "direct": peer.get("Direct", False),
            "latency_ms": (peer.get("Latency") or {}).get("PingMs"),
            "last_seen": peer.get("LastSeen"),
        }
        for subnet in peer.get("AdvertiseRoutes", []):
            routes.append({
                "subnet": subnet,
                "advertiser": peer_key,
                "accepted": True,
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
