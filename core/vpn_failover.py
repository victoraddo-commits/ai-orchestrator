"""TK-176d6efe: Proxmox B LAN health check.

Proxmox B is reached directly via LAN at 192.168.1.110 (no VPN tunnel).
This module provides a health check + recovery loop: if Proxmox B becomes
unreachable, retry a few times before alerting the operator.

Architecture:
    1. Health check — can we reach 192.168.1.110:8006?
    2. Retry up to MAX_RECOVERY_ATTEMPTS on failure (short, bounded probes)
    3. Emit alert when all retries exhausted

Recovery is a no-op when DISABLE_VPN_MONITORING is true so a scheduled
cycle can never stall retrying a dead host.
"""

import os
import socket
import time
from datetime import datetime, timezone

from core.logger import info

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Probe target — Proxmox B on the LAN (PVE-B, not the dead 192.168.1.109).
DEFAULT_PROBE_HOST = "192.168.1.110"
DEFAULT_PROBE_PORT = 8006


def _probe_host() -> str:
    return os.environ.get("VPN_FAILOVER_PROBE_HOST", "") or DEFAULT_PROBE_HOST


def _probe_port() -> int:
    return int(os.environ.get("VPN_FAILOVER_PROBE_PORT", "") or DEFAULT_PROBE_PORT)


# Backwards-compatible import-time snapshots; callers should prefer the
# helpers so a later env change is still honoured.
PROBE_HOST = _probe_host()
PROBE_PORT = _probe_port()

# Per-attempt TCP connect timeout (seconds). Kept short so a dead host can
# never tie up the scheduler for tens of seconds.
PROBE_TIMEOUT = float(os.environ.get("VPN_FAILOVER_PROBE_TIMEOUT", "2"))

# Max recovery attempts per cycle (prevents thrashing on transient network glitches)
MAX_RECOVERY_ATTEMPTS = int(os.environ.get("VPN_FAILOVER_MAX_ATTEMPTS", "2"))

# Seconds to wait between retry attempts
RETRY_DELAY = float(os.environ.get("VPN_FAILOVER_RETRY_DELAY", "1"))


def _monitoring_disabled() -> bool:
    """True when the operator has turned VPN failover off."""
    value = (os.environ.get("DISABLE_VPN_MONITORING", "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _proxmox_b_is_reachable(host: str | None = None,
                            port: int | None = None,
                            timeout: float | None = None) -> bool:
    """Return True if we can establish a TCP connection to Proxmox B."""
    host = host or _probe_host()
    port = port or _probe_port()
    timeout = PROBE_TIMEOUT if timeout is None else timeout
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def check_tunnel_health() -> dict:
    """Evaluate Proxmox B LAN reachability.

    Returns a dict suitable for logging and dashboard display:
        {ok: bool, host: str, port: int, reachable: bool,
         checked_at: iso8601}
    """
    host = _probe_host()
    port = _probe_port()
    reachable = _proxmox_b_is_reachable(host=host, port=port)
    return {
        "ok": reachable,
        "host": host,
        "port": port,
        "reachable": reachable,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def attempt_recovery() -> list[dict]:
    """Attempt to recover Proxmox B connectivity.

    No-op when DISABLE_VPN_MONITORING is set. Otherwise probes up to
    MAX_RECOVERY_ATTEMPTS times with a short timeout; an alert is appended
    only when all attempts fail.

    Returns:
        [] if monitoring is disabled or Proxmox B is reachable
        [recovered_event] when a retry succeeds
        [alert_event] if all retries exhausted
    """
    events: list[dict] = []

    if _monitoring_disabled():
        info("vpn_failover: DISABLE_VPN_MONITORING=true — skipping recovery")
        return events

    health = check_tunnel_health()
    if health["reachable"]:
        return events  # nothing to do

    host = health["host"]
    port = health["port"]
    info(f"vpn_failover: Proxmox B ({host}:{port}) unreachable — attempting recovery")

    for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
        info(f"vpn_failover: recovery attempt {attempt}/{MAX_RECOVERY_ATTEMPTS}")
        if attempt > 1 and RETRY_DELAY > 0:
            time.sleep(RETRY_DELAY)

        if _proxmox_b_is_reachable(host=host, port=port):
            events.append({
                "type": "vpn_recovered",
                "severity": "info",
                "component": "vpn_failover",
                "message": f"Proxmox B ({host}) recovered on attempt {attempt}.",
                "attempt": attempt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return events

    # All attempts exhausted
    events.append({
        "type": "vpn_down",
        "severity": "critical",
        "component": "vpn_failover",
        "message": (
            f"Proxmox B ({host}:{port}) is unreachable after "
            f"{MAX_RECOVERY_ATTEMPTS} attempts.  Manual intervention required."
        ),
        "health": health,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return events


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "health":
        print(_json.dumps(check_tunnel_health(), indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "recover":
        events = attempt_recovery()
        print(_json.dumps(events, indent=2, default=str))
        if not events:
            print("(no events — Proxmox B is reachable or monitoring disabled)")
    else:
        print("Usage: python -m core.vpn_failover {health|recover}")
