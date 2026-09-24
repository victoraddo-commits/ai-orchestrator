"""Reconciled network inventory + app-access map for the Command Center.

Reads the persisted topology graph (populated by the network discovery cycle)
and reshapes it for the "Network & Data" panel. The graph is the single source
of truth — this module never invents NICs, peers or policies.

* :func:`collect_nic_inventory` — per-host NIC / bridge / VLAN inventory plus
  the physical NICs available for WAN use. ``refresh=True`` runs the full
  discovery cycle and persists it first.
* :func:`app_access` — read-only "which service on which network" view. This is
  the *foundation* for the multi-WAN policy engine: it reports declared
  exposures but enforces nothing (no fabrications).
"""
from __future__ import annotations

from core.network_knowledge import load_graph

_OPNSENSE_NOTE = "not configured — no OPNsense API credentials wired"

# Declared, static service→network exposure map. This is a *design seed* for the
# multi-WAN policy engine, NOT enforced configuration: every entry is marked
# ``enforced: false`` and ``source: declared``. Update as real policy lands.
_DECLARED_EXPOSURES = [
    {"service": "orchestrator-api", "port": 8000,
     "networks": ["lan:192.168.1.0/24", "tailnet"], "enforced": False,
     "source": "declared"},
    {"service": "command-center", "port": 8080,
     "networks": ["tailnet"], "enforced": False, "source": "declared"},
    {"service": "legal-brain", "port": 8000,
     "networks": ["tailnet"], "enforced": False, "source": "declared"},
    {"service": "freellmapi", "port": 3001,
     "networks": ["lan:192.168.1.0/24"], "enforced": False, "source": "declared"},
    {"service": "wireguard-dashboard", "port": 51880,
     "networks": ["lan:192.168.1.0/24"], "enforced": False, "source": "declared"},
    {"service": "vm104-ollama", "port": 11434,
     "networks": ["loopback:127.0.0.1"], "enforced": False, "source": "declared"},
]


def _hosts_from_graph(graph: dict) -> list[dict]:
    hosts: list[dict] = []
    for key, site in (graph.get("sites") or {}).items():
        px = site.get("proxmox") or {}
        hosts.append({
            "site": key,
            "node": px.get("name"),
            "host": px.get("lan_ip") or site.get("gateway"),
            "tailscale_ip": px.get("tailscale_ip"),
            "online": bool(px.get("online")),
            "ssh_reachable": bool(px.get("ssh_reachable") or px.get("reachable")),
            "nics": px.get("nics") or [],
            "bridges": px.get("bridges") or [],
            "vlans": px.get("vlans") or [],
            "available_wan": px.get("available_wan") or [],
        })
    return hosts


def collect_nic_inventory(refresh: bool = False) -> dict:
    """NIC inventory for every site (optionally triggering a fresh discovery)."""
    graph = load_graph()
    if refresh:
        from core.network_discovery_cycle import run_network_discovery_cycle
        graph = run_network_discovery_cycle()
    hosts = _hosts_from_graph(graph)
    has_nics = any(h["nics"] for h in hosts)
    return {
        "hosts": hosts,
        "generated_at": graph.get("generated_at"),
        "last_discovery": graph.get("last_discovery"),
        "source": "topology graph",
        "note": None if has_nics else (
            "no NIC inventory persisted yet — run discovery"),
    }


def _wan_candidates(graph: dict) -> list[dict]:
    out: list[dict] = []
    for key, site in (graph.get("sites") or {}).items():
        px = site.get("proxmox") or {}
        for name in px.get("available_wan") or []:
            nic = next((n for n in px.get("nics") or [] if n.get("name") == name), {})
            out.append({
                "site": key, "node": px.get("name"), "nic": name,
                "mac": nic.get("mac"),
                "speed_mbps": nic.get("speed_mbps"),
                "state": "available",
            })
    return out


def app_access(graph: dict | None = None) -> dict:
    """Read-only service→network exposure view (multi-WAN policy foundation)."""
    graph = graph if graph is not None else load_graph()
    services: list[dict] = []
    for key, site in (graph.get("sites") or {}).items():
        for svc in site.get("services") or []:
            services.append({**svc, "site": key})
    return {
        "services": services,
        "declared_exposures": _DECLARED_EXPOSURES,
        "policy": {
            "engine": "multi-wan-policy",
            "status": "design-stub",
            "enforced": False,
            "note": "read-only foundation for the multi-WAN policy engine — "
                    "no policies are enforced here; declared entries are static.",
            "wan_candidates": _wan_candidates(graph),
            "wan_candidates_note": (
                "physical NICs that are down and unassigned — candidates for "
                "additional WAN uplinks. Bringing a link up requires a firewall "
                "/ OPNsense change and is intentionally out of scope."),
            "opnsense": {"configured": False, "note": _OPNSENSE_NOTE},
        },
    }
