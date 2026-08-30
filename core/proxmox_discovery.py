"""Network-aware Proxmox discovery — extends proxmox_registry with routing data.

Correlates Tailscale IP ↔ Proxmox node ↔ LAN IP ↔ subnets.
Uses existing proxmox_monitor.PROXMOX_NODES and _api_get for API calls.
"""

import os, json, subprocess
from datetime import datetime, timezone
from typing import Optional

from core.proxmox_monitor import PROXMOX_NODES, _api_get


# -------------------------------------------------------------------
# SSH helpers (same pattern as tailscale_discovery)
# -------------------------------------------------------------------

def _ssh(node: dict, cmd: str) -> tuple[str, str, int]:
    key = node.get("ssh_key", os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa"))
    full_cmd = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        f"root@{node['host']}",
        cmd,
    ]
    try:
        r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


# -------------------------------------------------------------------
# Per-node network discovery
# -------------------------------------------------------------------

def discover_node_networking(node: dict) -> dict:
    """Gather networking data for one Proxmox node via SSH."""
    result = {
        "node": node["name"],
        "reachable": False,
        "interfaces": [],
        "bridges": [],
        "vlans": [],
        "routing_table": [],
        "tailscale_ip": None,
        "lan_ip": None,
        "gateway": None,
    }

    # ip addr show
    stdout, _, rc = _ssh(node, "ip -j addr show")
    if rc == 0:
        try:
            for iface in json.loads(stdout):
                info = {"name": iface.get("ifname"), "ip": None, "mac": None}
                for addr_info in iface.get("addr_info", []):
                    info["ip"] = addr_info.get("local")
                info["mac"] = iface.get("address")
                result["interfaces"].append(info)
                # Identify LAN IP (non-loopback, non-tailscale)
                if info["ip"]:
                    ip = info["ip"]
                    if not ip.startswith(("10.", "100.", "127.")):
                        result["lan_ip"] = ip
        except json.JSONDecodeError:
            pass

    # ip route show
    stdout, _, rc = _ssh(node, "ip -j route show")
    if rc == 0:
        try:
            for route in json.loads(stdout):
                result["routing_table"].append({
                    "dst": route.get("dst", ""),
                    "gateway": route.get("gateway"),
                    "dev": route.get("dev"),
                    "table": route.get("table"),
                })
        except json.JSONDecodeError:
            pass

    # Tailscale IP detection
    stdout, _, rc = _ssh(node, "tailscale ip -4")
    if rc == 0:
        result["tailscale_ip"] = stdout.strip()

    # Default gateway
    stdout, _, rc = _ssh(node, "ip route show default")
    if rc == 0:
        parts = stdout.split()
        if "via" in parts:
            idx = parts.index("via")
            result["gateway"] = parts[idx + 1] if idx + 1 < len(parts) else None

    result["reachable"] = True
    return result


# -------------------------------------------------------------------
# Correlation
# -------------------------------------------------------------------

def _correlate_tailscale_to_node(ts_data: dict, px_nodes: dict) -> dict:
    """Match Tailscale peer IPs to Proxmox node configs."""
    for node_name, ts_peer in ts_data.items():
        if node_name in px_nodes:
            for peer_name, peer_info in (ts_peer.get("peers") or {}).items():
                ts_ip = peer_info.get("tailscale_ip")
                if ts_ip:
                    for px_name, px_node in px_nodes.items():
                        if px_node.get("tailscale_ip") == ts_ip:
                            px_nodes[px_name]["tailscale_peer"] = node_name
                            px_nodes[px_name]["role"] = peer_info.get("role")
    return px_nodes


def discover_all_nodes() -> dict:
    """Full network-aware Proxmox discovery across all configured nodes."""
    results = {}
    for node in PROXMOX_NODES:
        net = discover_node_networking(node)
        results[node["name"]] = net
    return results
