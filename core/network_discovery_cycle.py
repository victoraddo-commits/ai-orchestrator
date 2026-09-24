"""Network discovery cycle — wires tailscale + proxmox + topology + connectivity into one scheduled run."""

from core.tailscale_discovery import discover_all_nodes as discover_tailscale
from core.proxmox_discovery import discover_all_nodes as discover_proxmox
from core.topology_engine import build_graph, save, detect_changes, get_natural_summary
from core.connectivity_monitor import test_site_paths
from core.network_knowledge import load_graph
from core.logger import info
from core import incident_manager
from core import kai_event_bus

# Site definitions for connectivity testing (verified 2026-09-24).
SITE_A = {
    "name": "SITE-A",
    "tailscale_ip": "100.83.4.27",
    "gateway": "192.168.99.254",
    "proxmox_ip": "192.168.99.2",
}
SITE_B = {
    "name": "SITE-B",
    "tailscale_ip": "100.122.38.118",
    "gateway": "192.168.1.1",
    "proxmox_ip": "192.168.1.110",
}


def _tailnet_peer_state(ts_data: dict) -> dict[str, bool]:
    """Map tailnet IP → online across every discovered node's peer list."""
    state: dict[str, bool] = {}
    for node in (ts_data or {}).values():
        for peer in (node.get("peers") or {}).values():
            ip = peer.get("tailscale_ip")
            if ip:
                state[ip] = bool(peer.get("online"))
    return state


def run_network_discovery_cycle():
    """Run full network discovery: tailscale + proxmox + topology + connectivity."""
    info("network_discovery: cycle started")

    # 1. Discover
    ts_data = discover_tailscale()
    px_data = discover_proxmox()

    # 2. Build graph
    graph = build_graph(ts_data, px_data)

    # 3. Connectivity test (ICMP from the orchestrator; may be blind to the
    #    tailnet if this host has no tailscale seat).
    conn = test_site_paths(SITE_A, SITE_B)
    graph["connectivity"] = {
        "a_to_b_direct": conn.get("a_to_b_direct", "UNKNOWN"),
        "b_to_a_direct": conn.get("b_to_a_direct", "UNKNOWN"),
        "a_subnet_to_b_subnet": conn.get("a_subnet_to_b_subnet", "UNKNOWN"),
    }
    # Tunnel health is the real tailnet mesh state (both site peers online),
    # preferred over ICMP when the peer state is known.
    peer_state = _tailnet_peer_state(ts_data)
    a_up = peer_state.get(SITE_A["tailscale_ip"])
    b_up = peer_state.get(SITE_B["tailscale_ip"])
    if a_up is not None and b_up is not None:
        healthy = a_up and b_up
        graph["connectivity"]["peer_state"] = {
            SITE_A["tailscale_ip"]: a_up, SITE_B["tailscale_ip"]: b_up}
    else:
        healthy = conn.get("a_to_b_direct") == "PASS"
    graph["tunnel"] = {
        "status": "HEALTHY" if healthy else "DEGRADED",
        "a_to_b_latency_ms": conn.get("a_to_b_latency_ms"),
        "b_to_a_latency_ms": conn.get("b_to_a_latency_ms"),
        "packet_loss_pct": conn.get("packet_loss_pct", 0.0),
        "last_test": conn.get("last_test"),
    }

    # 4. Detect changes → emit alerts
    prior = load_graph()
    changes = []
    if prior:
        changes = detect_changes(prior, graph)
        for change in changes:
            _emit_alert(change)
            _publish_change(change)

    # 5. Save
    save(graph)

    info(f"network_discovery: cycle complete — {len(changes) if prior else 0} changes")
    return graph


_CRITICAL_CHANGE_TYPES = {"PEER_OFFLINE", "ROUTE_WITHDRAWN", "ROUTE_REJECTED"}


def _publish_change(change: dict):
    """Publish a network topology change on the event bus.

    ``detect_changes`` only returns diffs against the previous graph, so this
    is already genuine-change-only (no steady-state spam).
    """
    severity = (kai_event_bus.CRITICAL if change.get("type") in _CRITICAL_CHANGE_TYPES
                else kai_event_bus.INFORMATIONAL)
    try:
        kai_event_bus.publish("network.node.changed", dict(change),
                              source="network_discovery", severity=severity)
    except Exception as e:  # noqa: BLE001 — never let telemetry break discovery
        info(f"network_discovery: failed to publish change: {e}")


def _emit_alert(change: dict):
    """Emit a Kai incident for a network change event."""
    ctype = change.get("type", "")

    # Map change types to Kai alert types from incident_manager
    ALERT_MAP = {
        "PEER_OFFLINE": {
            "alert_type": "NETWORK_PEER_OFFLINE",
            "severity": "critical",
            "message": "Tailscale peer {node} went offline",
        },
        "PEER_ONLINE": {
            "alert_type": "NETWORK_PEER_ONLINE",
            "severity": "info",
            "message": "Tailscale peer {node} came online",
        },
        "ROUTE_ADVERTISED": {
            "alert_type": "NETWORK_ROUTE_ADVERTISED_BUT_NOT_ACCEPTED",
            "severity": "warning",
            "message": "Subnet route {subnet} advertised by {advertiser}",
        },
        "ROUTE_WITHDRAWN": {
            "alert_type": "NETWORK_SUBNET_UNREACHABLE",
            "severity": "critical",
            "message": "Subnet route {subnet} withdrawn",
        },
        "ROUTE_REJECTED": {
            "alert_type": "NETWORK_ROUTE_ADVERTISED_BUT_NOT_ACCEPTED",
            "severity": "warning",
            "message": "Subnet route {subnet} rejected",
        },
        "ROUTE_ACCEPTED": {
            "alert_type": "NETWORK_ROUTE_ACCEPTED",
            "severity": "info",
            "message": "Subnet route {subnet} accepted",
        },
        "NODE_DISCOVERED": {
            "alert_type": "NETWORK_NODE_DISCOVERED",
            "severity": "info",
            "message": "New node discovered: {node}",
        },
    }

    alert = ALERT_MAP.get(ctype)
    if not alert:
        return

    # Format message
    try:
        message = alert["message"].format(**change)
    except KeyError:
        message = alert["message"]

    # Call incident_manager to create incident
    # Re-raise ProductionMemoryWriteBlocked so tests can verify the call was made
    try:
        incident_manager.create_incident(
            service="network",
            issue=message,
            severity=alert["severity"],
        )
    except Exception as e:
        # Let test framework guard exceptions propagate; log everything else
        if "ProductionMemoryWriteBlocked" in type(e).__name__:
            raise
        info(f"network_discovery: failed to create incident: {e}")
