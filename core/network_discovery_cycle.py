"""Network discovery cycle — wires tailscale + proxmox + topology + connectivity into one scheduled run."""

from core.tailscale_discovery import discover_all_nodes as discover_tailscale
from core.proxmox_discovery import discover_all_nodes as discover_proxmox
from core.topology_engine import build_graph, save, detect_changes, get_natural_summary
from core.connectivity_monitor import test_site_paths
from core.network_knowledge import load_graph
from core.logger import info
from core import incident_manager

# Site definitions for connectivity testing
SITE_A = {
    "name": "SITE-A",
    "tailscale_ip": "100.83.4.27",
    "gateway": "192.168.99.254",
    "proxmox_ip": "192.168.99.2",
}
SITE_B = {
    "name": "SITE-B",
    "tailscale_ip": "100.89.97.76",
    "gateway": "192.168.1.1",
    "proxmox_ip": "192.168.1.109",
}


def run_discovery_cycle():
    """Run full network discovery: tailscale + proxmox + topology + connectivity."""
    info("network_discovery: cycle started")

    # 1. Discover
    ts_data = discover_tailscale()
    px_data = discover_proxmox()

    # 2. Build graph
    graph = build_graph(ts_data, px_data)

    # 3. Connectivity test
    conn = test_site_paths(SITE_A, SITE_B)
    graph["connectivity"] = {
        "a_to_b_direct": conn.get("a_to_b_direct", "UNKNOWN"),
        "b_to_a_direct": conn.get("b_to_a_direct", "UNKNOWN"),
        "a_subnet_to_b_subnet": conn.get("a_subnet_to_b_subnet", "UNKNOWN"),
    }
    graph["tunnel"] = {
        "status": "HEALTHY" if conn.get("a_to_b_direct") == "PASS" else "DEGRADED",
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

    # 5. Save
    save(graph)

    info(f"network_discovery: cycle complete — {len(changes) if prior else 0} changes")
    return graph


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
