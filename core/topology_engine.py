"""Topology graph builder + change detector + natural language summary.

Builds a correlated site/host/VM/LXC/service graph from Tailscale + Proxmox data.
Detects changes vs prior graph. Generates human-readable summaries.
"""

import os
from datetime import datetime, timezone

from core.network_knowledge import load_graph, save_graph, load_prior


# -------------------------------------------------------------------
# Graph building
# -------------------------------------------------------------------

def build_graph(ts_data: dict, px_data: dict) -> dict:
    """Build full topology graph from Tailscale + Proxmox discovery data."""
    graph = load_graph()
    now = datetime.now(timezone.utc).isoformat()

    # Sites
    for site_key, site_info in _build_sites(ts_data, px_data).items():
        graph["sites"][site_key] = site_info

    # Tailscale peers + routes
    graph["tailscale"]["peers"] = _build_peer_map(ts_data)
    graph["tailscale"]["subnet_routes"] = _build_route_map(ts_data)

    # Timestamps
    graph["last_discovery"] = now

    return graph


def _build_sites(ts_data: dict, px_data: dict) -> dict:
    """Build site hierarchy: Proxmox node → LAN → gateway → containers → services."""
    sites = {}

    # Known site definitions
    SITE_DEFS = {
        "SITE-A": {
            "lan_subnet": "192.168.99.0/24",
            "gateway": "192.168.99.254",
            "proxmox_name": "pve",
            "tailscale_ip": "100.83.4.27",
        },
        "SITE-B": {
            "lan_subnet": "192.168.1.0/24",
            "gateway": "192.168.1.1",
            "proxmox_name": "pve-b",
            "tailscale_ip": "100.89.97.76",
        },
    }

    for site_key, defs in SITE_DEFS.items():
        px_name = defs["proxmox_name"]
        site = {
            "name": site_key,
            "lan_subnet": defs["lan_subnet"],
            "gateway": defs["gateway"],
            "proxmox": {
                "name": px_name,
                "proxmox_ip": px_data.get(px_name, {}).get("lan_ip", ""),
                "tailscale_ip": defs["tailscale_ip"],
                "online": px_data.get(px_name, {}).get("reachable", False),
            },
            "lxcs": [],
            "vms": [],
            "services": [],
        }

        # Enrich with networking
        net_info = px_data.get(px_name, {})
        site["proxmox"]["lan_ip"] = net_info.get("lan_ip", "")
        site["proxmox"]["gateway"] = net_info.get("gateway", "")
        site["proxmox"]["routing_table"] = net_info.get("routing_table", [])
        site["proxmox"]["interfaces"] = net_info.get("interfaces", [])

        # Tailscale peer info
        ts_node = ts_data.get(px_name, {})
        peer_info = {}
        for pname, pinfo in (ts_node.get("peers") or {}).items():
            if pinfo.get("tailscale_ip") == defs["tailscale_ip"]:
                peer_info = pinfo
                break
        site["tailscale_peer"] = peer_info

        sites[site_key] = site

    return sites


def _build_peer_map(ts_data: dict) -> dict:
    """Flatten all Tailscale peers across all nodes into a single dict."""
    all_peers = {}
    for node_name, node_data in ts_data.items():
        for peer_name, peer_info in (node_data.get("peers") or {}).items():
            all_peers[peer_name] = peer_info
    return all_peers


def _build_route_map(ts_data: dict) -> dict:
    """Build subnet route map: subnet → {advertiser, peer, accepted}."""
    routes = {}
    for node_name, node_data in ts_data.items():
        for route in node_data.get("subnet_routes", []):
            routes[route["subnet"]] = {
                "advertiser": route["advertiser"],
                "accepted": route.get("accepted", True),
            }
    return routes


# -------------------------------------------------------------------
# Change detection
# -------------------------------------------------------------------

def detect_changes(prior: dict, current: dict) -> list[dict]:
    """Compare prior and current graphs. Returns list of ChangeEvent dicts."""
    changes = []
    now = datetime.now(timezone.utc).isoformat()

    # Peer state changes
    prior_peers = prior.get("tailscale", {}).get("peers", {})
    current_peers = current.get("tailscale", {}).get("peers", {})
    for name, info in current_peers.items():
        prior_info = prior_peers.get(name, {})
        if not prior_info:
            changes.append({"type": "NODE_DISCOVERED", "node": name, "at": now})
        elif not info.get("online") and prior_info.get("online"):
            changes.append({"type": "PEER_OFFLINE", "node": name, "at": now})
        elif info.get("online") and not prior_info.get("online"):
            changes.append({"type": "PEER_ONLINE", "node": name, "at": now})

    # Routes changes
    prior_routes = prior.get("tailscale", {}).get("subnet_routes", {})
    current_routes = current.get("tailscale", {}).get("subnet_routes", {})
    for subnet, info in current_routes.items():
        if subnet not in prior_routes:
            changes.append({"type": "ROUTE_ADVERTISED", "subnet": subnet, "advertiser": info["advertiser"], "at": now})
        elif not info.get("accepted") and prior_routes[subnet].get("accepted"):
            changes.append({"type": "ROUTE_REJECTED", "subnet": subnet, "at": now})
        elif info.get("accepted") and not prior_routes[subnet].get("accepted"):
            changes.append({"type": "ROUTE_ACCEPTED", "subnet": subnet, "at": now})
    for subnet in prior_routes:
        if subnet not in current_routes:
            changes.append({"type": "ROUTE_WITHDRAWN", "subnet": subnet, "at": now})

    return changes


# -------------------------------------------------------------------
# Natural language summary
# -------------------------------------------------------------------

def get_natural_summary(graph: dict) -> str:
    """Generate human-readable topology summary from graph."""
    sites = graph.get("sites", {})
    tunnel = graph.get("tunnel", {})
    routes = graph.get("tailscale", {}).get("subnet_routes", {})

    lines = []
    site_list = sorted(sites.keys())
    if len(site_list) >= 2:
        lines.append(f"{sites[site_list[0]]['name']} and {sites[site_list[1]]['name']} are connected through Tailscale.")

    for site_key, site in sites.items():
        px = site.get("proxmox", {})
        ts_ip = px.get("tailscale_ip", "?")
        lan = site.get("lan_subnet", "?")
        lxc_count = len(site.get("lxcs", []))
        vm_count = len(site.get("vms", []))
        lines.append(
            f"{site['name']} ({px.get('name', '?')}) at {ts_ip} routes {lan} "
            f"({lxc_count} LXCs, {vm_count} VMs)"
        )

    # Route status
    active_routes = [s for s, r in routes.items() if r.get("accepted")]
    if active_routes:
        lines.append(f"Active subnet routes: {', '.join(active_routes)}")

    # Tunnel status
    status = tunnel.get("status", "UNKNOWN")
    lat_a = tunnel.get("a_to_b_latency_ms") or "?"
    lat_b = tunnel.get("b_to_a_latency_ms") or "?"
    if status == "HEALTHY":
        lines.append(
            f"Site-to-site tunnel: HEALTHY. Latency A→B: {lat_a}ms, B→A: {lat_b}ms. "
            f"Packet loss: {tunnel.get('packet_loss_pct', 0)}%"
        )
    else:
        lines.append(f"Site-to-site tunnel: {status}")

    return " ".join(lines)


# -------------------------------------------------------------------
# Save helper
# -------------------------------------------------------------------

def save(graph: dict) -> None:
    """Save graph and update last_change timestamp if changes detected."""
    prior = load_prior()
    if prior:
        changes = detect_changes(prior, graph)
        if changes:
            graph["last_change"] = datetime.now(timezone.utc).isoformat()
    save_graph(graph)
