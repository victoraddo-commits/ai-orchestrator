"""TK-176d6efe: Proxmox B LAN health check.

Proxmox B is reached via the proxmox-b-tunnel SSH tunnel (localhost:8007).
This module provides a health check + recovery loop: if Proxmox B becomes
unreachable, retry a few times before alerting the operator.

Architecture:
    1. Health check — can we reach localhost:8007 (Proxmox B API via tunnel)?
    2. Retry up to MAX_RECOVERY_ATTEMPTS on failure
    3. Emit alert when all retries exhausted
"""

import os
import socket
import time
from datetime import datetime, timezone

from core.logger import info

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Probe target — Proxmox B API via the proxmox-b-tunnel SSH tunnel (localhost:8007)
PROBE_HOST = os.environ.get("VPN_FAILOVER_PROBE_HOST", "localhost")
PROBE_PORT = int(os.environ.get("VPN_FAILOVER_PROBE_PORT", "8007"))

# Max recovery attempts per cycle (prevents thrashing on transient network glitches)
MAX_RECOVERY_ATTEMPTS = int(os.environ.get("VPN_FAILOVER_MAX_ATTEMPTS", "3"))

# Seconds to wait between retry attempts
RETRY_DELAY = int(os.environ.get("VPN_FAILOVER_RETRY_DELAY", "10"))

# Seconds between repeated "still down" critical alerts when Proxmox B stays
# unreachable. Once a persistent outage is already known, re-running the full
# retry loop + re-alerting every scheduler cycle both blocks the cycle ~40s
# and spams the operator — so suppress repeats within this window. A single
# cheap probe per cycle still detects recovery.
ALERT_COOLDOWN = int(os.environ.get("VPN_FAILOVER_ALERT_COOLDOWN", "1800"))  # 30 min

# Module-level state: timestamp of the last "still down" critical alert.
_last_down_alert_at: float = 0.0


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _proxmox_b_is_reachable() -> bool:
    """Return True if we can establish a TCP connection to Proxmox B."""
    try:
        sock = socket.create_connection((PROBE_HOST, PROBE_PORT), timeout=10)
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
    reachable = _proxmox_b_is_reachable()
    return {
        "ok": reachable,
        "host": PROBE_HOST,
        "port": PROBE_PORT,
        "reachable": reachable,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def attempt_recovery() -> list[dict]:
    """Attempt to recover Proxmox B connectivity.

    Tries TCP probe up to MAX_RECOVERY_ATTEMPTS times.  Returns a list
    of event dicts; an alert is appended only when all attempts fail.

    Returns:
        [] if Proxmox B is reachable (no action needed)
        [alert_event] if all retries exhausted
    """
    global _last_down_alert_at

    events: list[dict] = []

    health = check_tunnel_health()
    if health["reachable"]:
        _last_down_alert_at = 0.0  # recovered — reset cooldown
        return events  # nothing to do

    # Persistently down: if we already alerted recently, skip the expensive
    # retry loop and don't re-alert. A single probe per cycle is enough to
    # notice recovery without blocking the scheduler or spamming Telegram.
    now = time.time()
    if _last_down_alert_at and (now - _last_down_alert_at) < ALERT_COOLDOWN:
        return events

    info(f"vpn_failover: Proxmox B ({PROBE_HOST}:{PROBE_PORT}) unreachable — attempting recovery")

    for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
        info(f"vpn_failover: recovery attempt {attempt}/{MAX_RECOVERY_ATTEMPTS}")
        time.sleep(RETRY_DELAY)

        if _proxmox_b_is_reachable():
            _last_down_alert_at = 0.0
            events.append({
                "type": "vpn_recovered",
                "severity": "info",
                "component": "vpn_failover",
                "message": f"Proxmox B ({PROBE_HOST}) recovered on attempt {attempt}.",
                "attempt": attempt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return events

    # All attempts exhausted
    _last_down_alert_at = now
    events.append({
        "type": "vpn_down",
        "severity": "critical",
        "component": "vpn_failover",
        "message": (
            f"Proxmox B ({PROBE_HOST}:{PROBE_PORT}) is unreachable after "
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
            print("(no events — Proxmox B is reachable)")
    else:
        print("Usage: python -m core.vpn_failover {health|recover}")
