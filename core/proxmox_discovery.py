"""Network-aware Proxmox discovery — extends proxmox_registry with routing data.

Correlates Tailscale IP ↔ Proxmox node ↔ LAN IP ↔ subnets.
Uses existing proxmox_monitor.PROXMOX_NODES and SSH commands (no API calls).
"""

import os, json, subprocess
from datetime import datetime, timezone
from typing import Optional

from core.proxmox_monitor import PROXMOX_NODES


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

    # Track per-command success for reachable check
    ssh_ok = False

    # ip addr show
    stdout, _, rc = _ssh(node, "ip -j addr show")
    if rc == 0:
        ssh_ok = True
        try:
            # RFC1918 private ranges + loopback + Tailscale/CGNAT carrier-grade NAT
            private_prefixes = ("10.", "100.", "127.", "172.16.", "172.17.", "172.18.",
                                "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                                "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                                "172.29.", "172.30.", "172.31.", "192.168.")
            for iface in json.loads(stdout):
                info = {"name": iface.get("ifname"), "ip": None, "mac": None}
                for addr_info in iface.get("addr_info", []):
                    info["ip"] = addr_info.get("local")
                info["mac"] = iface.get("address")
                result["interfaces"].append(info)
                # Identify LAN IP (prefer non-private, else first non-loopback/non-ts)
                if info["ip"]:
                    ip = info["ip"]
                    if not ip.startswith(private_prefixes):
                        result["lan_ip"] = ip
                        break
            # Fallback: grab first non-private-prefix IP if nothing better found
            if result["lan_ip"] is None:
                for iface in json.loads(stdout):
                    for addr_info in iface.get("addr_info", []):
                        ip = addr_info.get("local")
                        if ip and not ip.startswith(private_prefixes):
                            result["lan_ip"] = ip
                            break
                    if result["lan_ip"]:
                        break
        except json.JSONDecodeError:
            # JSON parse failed — interface data unavailable, continue without it
            pass

    # ip route show
    stdout, _, rc = _ssh(node, "ip -j route show")
    if rc == 0:
        ssh_ok = True
        try:
            for route in json.loads(stdout):
                result["routing_table"].append({
                    "dst": route.get("dst", ""),
                    "gateway": route.get("gateway"),
                    "dev": route.get("dev"),
                    "table": route.get("table"),
                })
        except json.JSONDecodeError:
            # JSON parse failed — routing table unavailable, continue without it
            pass

    # Tailscale IP detection
    stdout, _, rc = _ssh(node, "tailscale ip -4")
    if rc == 0:
        ssh_ok = True
        result["tailscale_ip"] = stdout.strip()

    # Default gateway
    stdout, _, rc = _ssh(node, "ip route show default")
    if rc == 0:
        ssh_ok = True
        parts = stdout.split()
        if "via" in parts:
            idx = parts.index("via")
            result["gateway"] = parts[idx + 1] if idx + 1 < len(parts) else None

    result["reachable"] = ssh_ok
    return result


# -------------------------------------------------------------------
# Correlation
# -------------------------------------------------------------------

def _correlate_tailscale_to_node(ts_data: dict, px_nodes: dict) -> dict:
    """Match Tailscale peer IPs to Proxmox node configs.

    Builds a lookup dict keyed by tailscale_ip (O(m) instead of O(n×m)),
    then annotates matching px_nodes in-place and returns the same dict.
    """
    # Build tailscale_ip → {node_name, role} lookup
    ts_ip_map: dict[str, dict] = {}
    for node_name, ts_peer in ts_data.items():
        for peer_name, peer_info in (ts_peer.get("peers") or {}).items():
            ts_ip = peer_info.get("tailscale_ip")
            if ts_ip:
                ts_ip_map[ts_ip] = {"tailscale_peer": node_name, "role": peer_info.get("role")}

    # Annotate px_nodes in-place
    for px_name, px_node in px_nodes.items():
        match = ts_ip_map.get(px_node.get("tailscale_ip"))
        if match:
            px_nodes[px_name]["tailscale_peer"] = match["tailscale_peer"]
            px_nodes[px_name]["role"] = match["role"]

    return px_nodes


def discover_all_nodes() -> dict:
    """Full network-aware Proxmox discovery across all configured nodes."""
    results = {}
    for node in PROXMOX_NODES:
        net = discover_node_networking(node)
        results[node["name"]] = net
    return results
