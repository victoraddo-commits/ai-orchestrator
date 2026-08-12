"""TK-176d6efe: VPN failover for Proxmox B (WireGuard tunnel health + recovery).

Detects when the WireGuard tunnel to Proxmox B is down and attempts
automatic recovery.  Designed to work with both OPNsense-terminated and
locally-terminated WireGuard tunnels.

Architecture:
    1. Health check — can we reach 10.8.0.5:8006?
    2. Attempt recovery — restart wg interface, try alternate endpoint
    3. Escalate — alert operator if recovery fails

WireGuard on this host (LXC container) is managed through the standard
wg/wg-quick tools.  The tunnel config is expected at
/etc/wireguard/wg-proxmox-b.conf (created by the operator).

If no local WireGuard interface exists, recovery falls back to alerting
only — the assumption is that the primary tunnel is on OPNsense and the
operator must intervene.
"""

import os
import subprocess
import time
from datetime import datetime, timezone

from core.logger import info
from core.proxmox_monitor import PROXMOX_NODES, get_vpn_status

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# WG interface name for the optional local tunnel to Proxmox B
WG_INTERFACE = os.environ.get("VPN_FAILOVER_WG_IFACE", "wg-proxmox-b")

# How long to wait for WG to come up after (re)start (seconds)
WG_UP_TIMEOUT = int(os.environ.get("VPN_FAILOVER_WG_UP_TIMEOUT", "30"))

# Probe target — must be a host reachable through the WG tunnel
PROBE_HOST = os.environ.get("VPN_FAILOVER_PROBE_HOST", "10.250.0.2")
PROBE_PORT = int(os.environ.get("VPN_FAILOVER_PROBE_PORT", "11434"))

# Max recovery attempts per cycle (prevents thrashing)
MAX_RECOVERY_ATTEMPTS = int(os.environ.get("VPN_FAILOVER_MAX_ATTEMPTS", "2"))


# ---------------------------------------------------------------------------
# Tunnel health
# ---------------------------------------------------------------------------

def _proxmox_b_is_reachable() -> bool:
    """Check if Proxmox B's API port is reachable via any path.

    First checks the cached VPN status (populated by collect_node_health).
    If the cache is empty (e.g. first cycle after restart), falls back to
    a quick TCP check of the probe host.
    """
    vpn = get_vpn_status("pve-b")
    status = vpn.get("pve-b", {})
    if status:
        return status.get("reachable", False)

    # Cache empty — do a quick TCP reachability probe (no auth needed).
    import socket
    try:
        sock = socket.create_connection((PROBE_HOST, PROBE_PORT), timeout=5)
        sock.close()
        # Populate the cache so subsequent calls don't probe again.
        _cache_set_reachable(True)
        return True
    except OSError:
        _cache_set_reachable(False)
        return False


def _cache_set_reachable(reachable: bool) -> None:
    """Set the VPN status cache entry for pve-b."""
    from core.proxmox_monitor import _vpn_status_cache
    from datetime import datetime, timezone
    _vpn_status_cache["pve-b"] = {
        "host_used": PROBE_HOST,
        "reachable": reachable,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 1,
        "error": None if reachable else "connection_failure",
    }


def _wg_interface_exists() -> bool:
    """Check whether the local WireGuard interface is configured."""
    try:
        r = subprocess.run(
            ["wg", "show", WG_INTERFACE],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _wg_is_up() -> bool:
    """Check if the local WG interface has recent handshake data (is alive)."""
    try:
        r = subprocess.run(
            ["wg", "show", WG_INTERFACE, "latest-handshakes"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return False
        # If any peer has a handshake within the last 5 minutes, tunnel is alive
        now = int(time.time())
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    ts = int(parts[1])
                    if now - ts < 300:
                        return True
                except ValueError:
                    continue
        return False
    except Exception:
        return False


def check_tunnel_health() -> dict:
    """Evaluate the WireGuard tunnel health for Proxmox B.

    Returns a dict suitable for logging and dashboard display:
        {ok: bool, interface: str|None, wg_up: bool, proxmox_reachable: bool,
         recovery_needed: bool, checked_at: iso8601}
    """
    result = {
        "ok": True,
        "interface": WG_INTERFACE if _wg_interface_exists() else None,
        "wg_up": False,
        "proxmox_reachable": _proxmox_b_is_reachable(),
        "recovery_needed": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if result["interface"]:
        result["wg_up"] = _wg_is_up()

    # Recovery is needed when Proxmox B is unreachable AND we have a local
    # WG interface to try (or when the local WG is down but configured).
    if not result["proxmox_reachable"]:
        result["ok"] = False
        if result["interface"] and not result["wg_up"]:
            result["recovery_needed"] = True

    return result


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def _restart_wg_interface() -> bool:
    """Bring the WG interface down and back up via wg-quick.

    Returns True if the interface shows handshake activity after restart.
    """
    info(f"vpn_failover: restarting WireGuard interface {WG_INTERFACE}")

    try:
        # Tear down
        subprocess.run(
            ["wg-quick", "down", WG_INTERFACE],
            capture_output=True, text=True, timeout=15,
        )
        time.sleep(2)

        # Bring up
        up = subprocess.run(
            ["wg-quick", "up", WG_INTERFACE],
            capture_output=True, text=True, timeout=30,
        )
        if up.returncode != 0:
            info(f"vpn_failover: wg-quick up failed: {up.stderr.strip()[:200]}")
            return False

        # Wait for handshake
        deadline = time.time() + WG_UP_TIMEOUT
        while time.time() < deadline:
            if _wg_is_up():
                info(f"vpn_failover: {WG_INTERFACE} is up with handshake")
                return True
            time.sleep(3)

        info(f"vpn_failover: {WG_INTERFACE} up but no handshake after {WG_UP_TIMEOUT}s")
        return False

    except FileNotFoundError:
        info("vpn_failover: wg-quick not found — cannot restart tunnel")
        return False
    except Exception as exc:
        info(f"vpn_failover: wg restart error: {type(exc).__name__}: {exc}")
        return False


def attempt_recovery() -> list[dict]:
    """Attempt VPN failover recovery for Proxmox B.

    Recovery strategy (tried in order):
        1. If local WG interface exists, restart it
        2. (Future) ping the WG peer directly to force handshake refresh
        3. If nothing can be done, return an alert event

    Returns a list of event dicts for logging and alerting.
    """
    events: list[dict] = []

    health = check_tunnel_health()

    if health["proxmox_reachable"]:
        # No recovery needed — Proxmox B is reachable through some path
        return events

    if not health["recovery_needed"] and not health["interface"]:
        # No local WG interface to recover — tunnel is presumably on OPNsense.
        # Alert the operator but don't try to restart something we don't manage.
        events.append({
            "type": "vpn_down",
            "severity": "warning",
            "component": "vpn_failover",
            "message": (
                "Proxmox B (10.250.0.2) is unreachable via ZeroTier backbone.  "
                "ZeroTier interface is managed by network-core-b LXC"
                " — check network-core-b and ZeroTier Central."
                " (Replaced old DD-WRT WireGuard path 2026-08-11.)"
            ),
            "health": health,
        })
        return events

    # Attempt recovery — restart the local WG interface
    for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
        info(f"vpn_failover: recovery attempt {attempt}/{MAX_RECOVERY_ATTEMPTS}")

        success = _restart_wg_interface()

        events.append({
            "type": "wg_restart",
            "component": "vpn_failover",
            "interface": WG_INTERFACE,
            "attempt": attempt,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if success:
            events.append({
                "type": "vpn_recovered",
                "severity": "info",
                "component": "vpn_failover",
                "message": f"WireGuard tunnel {WG_INTERFACE} recovered on attempt {attempt}.",
            })
            return events

        if attempt < MAX_RECOVERY_ATTEMPTS:
            time.sleep(5 * attempt)

    # All attempts exhausted — escalate
    events.append({
        "type": "vpn_recovery_failed",
        "severity": "critical",
        "component": "vpn_failover",
        "message": (
            f"WireGuard tunnel {WG_INTERFACE} failed to recover after "
            f"{MAX_RECOVERY_ATTEMPTS} attempt(s).  Proxmox B is unreachable.  "
            "Manual intervention required — check the WireGuard config on "
            "both endpoints."
        ),
    })

    return events


# ---------------------------------------------------------------------------
# Config template helper (one-shot: run by operator to scaffold the tunnel)
# ---------------------------------------------------------------------------

WG_CONF_TEMPLATE = """\
# WireGuard tunnel: {iface}  →  Proxmox B
# Generated by ai-orchestrator vpn_failover.py — fill in the placeholders.

[Interface]
PrivateKey = <PASTE-PRIVATE-KEY-HERE>
Address = {address}

# Proxmox B WireGuard peer
[Peer]
PublicKey = <PASTE-PROXMOX-B-PUBLIC-KEY-HERE>
Endpoint = {endpoint}
AllowedIPs = 10.8.0.5/32
PersistentKeepalive = 25
"""


def generate_config_template(iface=WG_INTERFACE, address="10.8.0.3/32",
                             endpoint="10.8.0.5:51820"):
    """Print (not write) a WireGuard config template the operator can fill in.

    The operator runs this once to scaffold the tunnel config file:
        python -m core.vpn_failover generate-config > /etc/wireguard/{iface}.conf
        # edit to fill in keys
        chmod 600 /etc/wireguard/{iface}.conf
        wg-quick up {iface}
    """
    return WG_CONF_TEMPLATE.format(iface=iface, address=address, endpoint=endpoint)


# ---------------------------------------------------------------------------
# CLI entry point (for operator setup)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "generate-config":
        print(generate_config_template())
    elif len(sys.argv) > 1 and sys.argv[1] == "health":
        import json as _json
        print(_json.dumps(check_tunnel_health(), indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "recover":
        import json as _json
        events = attempt_recovery()
        print(_json.dumps(events, indent=2, default=str))
    else:
        print("Usage: python -m core.vpn_failover {generate-config|health|recover}")
