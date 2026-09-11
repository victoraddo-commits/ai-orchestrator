"""TK-176d6efe: Tailscale subnet-route health monitor.

Since 2026-09-11 the Site A ↔ Site B path runs over Tailscale subnet routes,
not the OPNsense WireGuard tunnel. This module watches the tailscale peers
that advertise the two LAN subnets we depend on, plus a ping to the Site B
gateway, and produces a health verdict.

Complements ``core.vpn_failover`` (which still checks the Proxmox B API
tunnel on localhost:8007). Both feed ``/api/vpn/*`` endpoints.

Health record shape (persisted to memory/vpn_failover_health.json):

    {
      "ts": "2026-09-11T21:45:00+00:00",
      "verdict": "healthy" | "degraded" | "down",
      "detail": "...",
      "peers": {
        "192.168.99.0/24": {"peer": "pve-1", "online": True,  "accepted": True},
        "192.168.1.0/24":  {"peer": "pve-2", "online": True,  "accepted": True},
      },
      "ping": {"target": "192.168.1.1", "ok": True, "rtt_ms": 220.5},
    }

Terminology: PrimaryRoutes on a peer means we accept and route through it.
AdvertisedRoutes means the peer offers it but our node may not have accepted.
Both need to be true for the route to actually carry traffic.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from core.memory import load, save

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The two subnet routes we depend on for Site A ↔ Site B LAN traffic.
EXPECTED_ROUTES: dict[str, str] = {
    "192.168.99.0/24": "pve-1",   # Site A LAN (advertised by ProxA)
    "192.168.1.0/24":  "pve-2",   # Site B LAN (advertised by ProxB)
}

# A single "canary" host on the Site B LAN we ping to confirm the route
# actually forwards traffic — Cloudflare/anycast gateway is not stable, so
# we pick 192.168.1.1 (Site B gateway per network_topology.json).
PING_TARGET: str = "192.168.1.1"

# Retain at most this many records in the history file.
MAX_HISTORY: int = 500

# Memory key.
_STORE_KEY = "vpn_failover_health"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _tailscale_status() -> dict[str, Any] | None:
    """Return the local tailscaled status --json, or None on failure.

    Uses subprocess.run with a short timeout so a hung tailscaled can't
    block the health cycle. Failures are treated as "down".
    """
    try:
        r = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _ping(target: str, timeout_sec: int = 3) -> tuple[bool, float | None]:
    """Single ICMP echo. Returns (ok, rtt_ms | None).

    Uses ``-c 1 -W <timeout>`` for a fast probe; treats non-zero exit as
    failure so a missing/blocked path is reported as down rather than
    hanging the cycle.
    """
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_sec), target],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None
    if r.returncode != 0:
        return False, None
    # Extract "time=X ms" from ping output.
    for line in r.stdout.splitlines():
        if "time=" in line:
            try:
                after = line.split("time=", 1)[1]
                num = after.split(" ", 1)[0]
                return True, float(num)
            except (IndexError, ValueError):
                return True, None
    return True, None


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def _peer_routes_from_status(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Given ``tailscale status --json`` output, return per-expected-route
    info: which peer advertises it, whether the peer is online, whether
    we accept it (PrimaryRoutes).

    Search by CIDR presence in PrimaryRoutes/AdvertisedRoutes rather than
    by peer HostName — multiple Proxmox nodes can share the same HostName
    (Tailscale doesn't force uniqueness), and the route itself is the
    invariant we care about.

    The expected-peer name recorded in EXPECTED_ROUTES is a labeling hint;
    if we find the CIDR on any peer we take that peer's actual name.
    """
    result: dict[str, dict[str, Any]] = {}
    peers = (status.get("Peer") or {}).values() if status else []
    for cidr, expected_peer in EXPECTED_ROUTES.items():
        info: dict[str, Any] = {
            "peer": expected_peer,
            "online": False,
            "accepted": False,
            "advertised": False,
        }
        # Prefer a peer that has this CIDR in PrimaryRoutes (accepted).
        match: dict[str, Any] | None = None
        for peer in peers:
            if cidr in (peer.get("PrimaryRoutes") or []):
                match = peer
                break
        # Fall back: any peer that advertises it (offered but not accepted).
        if match is None:
            for peer in peers:
                if cidr in (peer.get("AdvertisedRoutes") or []):
                    match = peer
                    break
        if match is not None:
            info["online"] = bool(match.get("Online"))
            info["advertised"] = cidr in (match.get("AdvertisedRoutes") or [])
            info["accepted"] = cidr in (match.get("PrimaryRoutes") or [])
            actual_name = match.get("HostName") or match.get("DNSName") or expected_peer
            info["peer"] = actual_name
        result[cidr] = info
    return result


def _verdict(
    status: dict[str, Any] | None,
    ping_ok: bool,
    peer_routes: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """Return (verdict, human-readable detail)."""
    if status is None:
        return "down", "tailscale status --json unavailable (daemon down or command missing)"

    missing_routes: list[str] = []
    offline_peers: list[str] = []
    for cidr, info in peer_routes.items():
        if not info["accepted"]:
            missing_routes.append(f"{cidr} (via {info['peer']})")
        if not info["online"]:
            offline_peers.append(info["peer"])

    if missing_routes and offline_peers:
        return "down", f"missing routes {missing_routes} and offline peers {offline_peers}"
    if offline_peers:
        return "down", f"offline peers: {sorted(set(offline_peers))}"
    if missing_routes:
        return "degraded", f"unaccepted routes: {missing_routes}"
    if not ping_ok:
        return "degraded", f"routes healthy but ping {PING_TARGET} failed — Site B may be unreachable at L3"
    return "healthy", "routes accepted, peers online, Site B gateway reachable"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_history() -> list[dict[str, Any]]:
    data = load(_STORE_KEY)
    if isinstance(data, dict):
        recs = data.get("records", [])
        return recs if isinstance(recs, list) else []
    if isinstance(data, list):
        return data
    return []


def _save_history(records: list[dict[str, Any]]) -> None:
    save(_STORE_KEY, {"schema_version": 1, "records": records[-MAX_HISTORY:]})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_once() -> dict[str, Any]:
    """Run a single health cycle. Appends a record to memory and returns it."""
    status = _tailscale_status()
    peer_routes = _peer_routes_from_status(status or {})
    ping_ok, rtt_ms = _ping(PING_TARGET)
    verdict, detail = _verdict(status, ping_ok, peer_routes)

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "detail": detail,
        "peers": peer_routes,
        "ping": {"target": PING_TARGET, "ok": ping_ok, "rtt_ms": rtt_ms},
    }

    history = _load_history()
    prev_verdict = history[-1]["verdict"] if history else None
    history.append(record)
    _save_history(history)

    # On healthy → degraded/down transition, record an incident so the
    # network dashboard / notifications flow surfaces it.
    if prev_verdict == "healthy" and verdict in ("degraded", "down"):
        try:
            from core.incident_manager import (
                load_incidents,
                save_incidents,
                find_open_duplicate,
                NETWORK_SUBNET_UNREACHABLE,
            )
            issue = f"tailscale subnet-route S2S {verdict}: {detail}"
            incidents = load_incidents()
            if not find_open_duplicate(incidents, "vpn_health_monitor", issue):
                incidents.append({
                    "type": NETWORK_SUBNET_UNREACHABLE,
                    "service": "vpn_health_monitor",
                    "issue": issue,
                    "severity": "critical" if verdict == "down" else "warning",
                    "status": "open",
                    "created_at": record["ts"],
                    "detail": detail,
                    "peers": peer_routes,
                })
                save_incidents(incidents)
        except Exception:
            # Never let incident logging break the health cycle.
            pass

    return record


def latest() -> dict[str, Any] | None:
    """Return the most recent health record, or None if never run."""
    history = _load_history()
    return history[-1] if history else None


def history(limit: int = 20) -> list[dict[str, Any]]:
    """Return the last ``limit`` records, newest first."""
    h = _load_history()
    return list(reversed(h[-limit:]))
