"""WireGuard management API routes.

Part of: Kai Mobile Command Node — Sub-project 5: WireGuard Resilience.

Exposes WireGuard tunnel status, peer management, and recovery operations
over the Kai API (port 8000).  All write operations require operator auth
once 15A (Platform Auth Foundation) is complete.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.wireguard_manager import (
    check_tunnel_to_proxmox_b,
    collect_wg_health_metrics,
    get_failover_state,
    get_wg_status,
    list_peers,
    attempt_full_recovery,
    restart_interface,
    set_peer_endpoint,
    WG_INTERFACE as _WG_INTERFACE,
    WG_PRIMARY_ENDPOINT as _WG_PRIMARY_ENDPOINT,
    WG_FALLBACK_ENDPOINT as _WG_FALLBACK_ENDPOINT,
)

router = APIRouter(prefix="/kai/wireguard", tags=["wireguard"])


# ---------------------------------------------------------------------------
# Read endpoints — status, peers, health
# ---------------------------------------------------------------------------


@router.get("/status")
def api_wg_status():
    """Full WireGuard interface status from the DD-WRT router.

    Returns interface info, all peers with handshake ages, transfer stats,
    and endpoint details.
    """
    return get_wg_status()


@router.get("/peers")
def api_wg_peers():
    """List all WireGuard peers with current status."""
    peers = list_peers()
    return {
        "interface": _WG_INTERFACE,
        "peer_count": len(peers),
        "peers": peers,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tunnel/proxmox-b")
def api_tunnel_check():
    """Check if the WireGuard tunnel to Proxmox B is healthy.

    Performs a TCP connect to Proxmox B (10.8.0.102:8006) through the
    WireGuard tunnel.  This is the definitive health check — if Proxmox B
    responds, the tunnel is working regardless of what wg show says.
    """
    return check_tunnel_to_proxmox_b()


@router.get("/health")
def api_wg_health():
    """Collect all WireGuard health metrics.

    Returns the same metric set that the health worker feeds into the
    health observatory for anomaly detection and trend analysis.
    """
    metrics = collect_wg_health_metrics()
    return {
        "metrics": metrics,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/failover")
def api_failover_state():
    """Current endpoint failover state.

    Shows which endpoint (primary/fallback) is active, when the last
    switch occurred, and failure counters for both endpoints.
    """
    return get_failover_state()


# ---------------------------------------------------------------------------
# Write endpoints — peer management, recovery
# ---------------------------------------------------------------------------


@router.post("/peer/endpoint")
def api_set_peer_endpoint(
    public_key: str = Query(..., description="WireGuard peer public key"),
    endpoint: str = Query(..., description="New endpoint (IP:PORT or HOST:PORT)"),
    confirm: bool = Query(default=False, description="Set to true to execute"),
):
    """Change a WireGuard peer's endpoint on the DD-WRT router.

    Use cases:
    - Manual endpoint failover
    - Changing the peer's external IP/port
    - Testing alternative endpoints

    Requires explicit confirm=true to execute.
    """
    if not confirm:
        return {
            "dry_run": True,
            "public_key": public_key,
            "new_endpoint": endpoint,
            "hint": "Set confirm=true to execute",
        }

    result = set_peer_endpoint(public_key, endpoint)
    if not result["ok"] and result["error"]:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@router.post("/restart")
def api_restart_interface(
    confirm: bool = Query(default=False, description="Set to true to execute"),
):
    """Restart the WireGuard interface on the DD-WRT router.

    Runs: ifconfig wg0 down; sleep 2; ifconfig wg0 up
    Requires explicit confirm=true to execute.

    Warning: This will briefly interrupt all VPN traffic through wg0.
    """
    if not confirm:
        return {
            "dry_run": True,
            "interface": _WG_INTERFACE,
            "hint": "Set confirm=true to execute and restart the interface",
        }

    result = restart_interface()
    if not result["ok"] and result["error"]:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@router.post("/recover")
def api_full_recovery(
    peer_public_key: Optional[str] = Query(
        default=None,
        description="Specific peer's public key for endpoint failover (auto-detects stalest if omitted)",
    ),
    confirm: bool = Query(default=False, description="Set to true to execute"),
):
    """Run the full WireGuard recovery sequence.

    Sequence:
    1. Verify tunnel is actually down (TCP probe to Proxmox B)
    2. If WG interface is down on DD-WRT, restart it
    3. If peers have no recent handshake, try endpoint failover
    4. If nothing works, escalate

    Requires explicit confirm=true to execute.
    """
    if not confirm:
        return {
            "dry_run": True,
            "peer_public_key": peer_public_key or "(auto-detect stalest)",
            "sequence": [
                "verify_tunnel_down",
                "restart_if_down",
                "endpoint_failover",
                "last_resort_restart",
                "escalate",
            ],
            "hint": "Set confirm=true to execute the full recovery sequence",
        }

    result = attempt_full_recovery(peer_public_key)
    if not result["ok"] and not result.get("tunnel_recovered"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Recovery failed — tunnel still down"),
        )

    return result


@router.get("/config")
def api_config():
    """Show current WireGuard configuration (no secrets exposed)."""
    return {
        "interface": _WG_INTERFACE,
        "router": "DD-WRT at 192.168.99.66:23",
        "tunnel_subnet": "10.8.0.0/24",
        "primary_endpoint_configured": bool(_WG_PRIMARY_ENDPOINT),
        "primary_endpoint": _WG_PRIMARY_ENDPOINT or "(not set)",
        "fallback_endpoint_configured": bool(_WG_FALLBACK_ENDPOINT),
        "fallback_endpoint": _WG_FALLBACK_ENDPOINT or "(not set)",
    }
