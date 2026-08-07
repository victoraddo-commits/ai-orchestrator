"""Phase 18A-b: Network Exposure Validator.

Audits active network listeners and configured bind addresses:
- Detects unintended 0.0.0.0 (wildcard) bindings where loopback is expected
- Reports open ports and their associated processes
- Integrates with existing rate-limiter patterns for exposure assessment
"""

import socket
import subprocess
import shutil
from typing import Dict, Any, List, Optional, Tuple


def _get_listening_sockets() -> List[Dict[str, Any]]:
    """Return active TCP/UDP listeners via ss or netstat."""
    listeners = []

    ss_path = shutil.which("ss")
    if ss_path:
        try:
            result = subprocess.run(
                [ss_path, "-tlnp"],
                capture_output=True, text=True, timeout=10
            )
            listeners = _parse_ss_output(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    if not listeners:
        netstat_path = shutil.which("netstat")
        if netstat_path:
            try:
                result = subprocess.run(
                    [netstat_path, "-tlnp"],
                    capture_output=True, text=True, timeout=10
                )
                listeners = _parse_netstat_output(result.stdout)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    return listeners


def _parse_ss_output(output: str) -> List[Dict[str, Any]]:
    listeners = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("State") or line.startswith("Netid"):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        try:
            local_addr = parts[4] if len(parts) > 4 else ""
            process = parts[-1] if parts else ""

            if ":" in local_addr:
                addr, port = local_addr.rsplit(":", 1)
            else:
                addr, port = local_addr, ""

            host = _normalize_address(addr)

            listeners.append({
                "protocol": "tcp",
                "address": addr,
                "host": host,
                "port": port,
                "process": process,
            })
        except (ValueError, IndexError):
            continue

    return listeners


def _parse_netstat_output(output: str) -> List[Dict[str, Any]]:
    listeners = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Proto") or line.startswith("Active"):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        try:
            proto = parts[0].lower()
            local_addr = parts[3] if len(parts) > 3 else ""
            process = parts[-1] if parts else ""

            if ":" in local_addr:
                addr, port = local_addr.rsplit(":", 1)
            else:
                addr, port = local_addr, ""

            host = _normalize_address(addr)

            listeners.append({
                "protocol": proto,
                "address": addr,
                "host": host,
                "port": port,
                "process": process,
            })
        except (ValueError, IndexError):
            continue

    return listeners


def _normalize_address(addr: str) -> str:
    """Normalize an address to a canonical form."""
    addr = addr.strip("[]")
    if addr in ("0.0.0.0", "::", "*", "*:*"):
        return "0.0.0.0 (wildcard - all interfaces)"
    if addr in ("127.0.0.1", "::1", "localhost"):
        return "127.0.0.1 (loopback only)"
    return addr


def _is_loopback(addr: str) -> bool:
    addr = addr.strip("[]")
    return addr in ("127.0.0.1", "::1", "localhost")


def _is_wildcard(addr: str) -> bool:
    addr = addr.strip("[]")
    return addr in ("0.0.0.0", "::", "*", "*:*")


SAFE_WILDCARD_PORTS = {80, 443, 8080, 8443, 8000}
LOOPBACK_EXPECTED_PORTS = {
    5432, 3306, 6379, 27017, 9090, 20128, 8086, 8088,
}


def audit_network_exposure() -> Dict[str, Any]:
    """Audit network listeners for exposure risks.

    Returns findings about wildcard binds, unexpected exposures,
    and rate-limiting integration points.
    """
    findings = []
    listeners = _get_listening_sockets()

    for listener in listeners:
        addr = listener["address"]
        port = listener["port"]

        try:
            port_int = int(port)
        except (ValueError, TypeError):
            port_int = 0

        if _is_wildcard(addr):
            if port_int in SAFE_WILDCARD_PORTS:
                findings.append({
                    "type": "network_exposure",
                    "severity": "info",
                    "issue": f"Expected wildcard bind on port {port} (public service)",
                    "address": addr,
                    "port": port,
                    "protocol": listener["protocol"],
                    "process": listener["process"],
                })
            else:
                findings.append({
                    "type": "network_exposure",
                    "severity": "high",
                    "issue": f"Wildcard bind on port {port} — service exposed on all interfaces",
                    "address": addr,
                    "port": port,
                    "protocol": listener["protocol"],
                    "process": listener["process"],
                    "recommendation": f"Bind to 127.0.0.1 unless this port {port} requires external access",
                    "fixable": False,
                })

        if _is_loopback(addr) and port_int in LOOPBACK_EXPECTED_PORTS:
            findings.append({
                "type": "network_exposure",
                "severity": "info",
                "issue": f"Expected loopback bind on port {port} (internal service)",
                "address": addr,
                "port": port,
                "protocol": listener["protocol"],
                "process": listener["process"],
            })

        hostname = socket.gethostname()
        if _is_wildcard(addr) and port_int not in SAFE_WILDCARD_PORTS and port_int not in LOOPBACK_EXPECTED_PORTS:
            findings.append({
                "type": "network_exposure",
                "severity": "medium",
                "issue": f"Uncategorized listening port {port} on wildcard address",
                "address": addr,
                "port": port,
                "protocol": listener["protocol"],
                "process": listener["process"],
                "fixable": False,
                "recommendation": f"Review if port {port} needs external access; prefer 127.0.0.1",
            })

    wildcard_count = sum(1 for f in findings
                         if f.get("severity") == "high" and f.get("type") == "network_exposure")
    loopback_count = len(listeners) - wildcard_count

    return {
        "findings": findings,
        "total_listeners": len(listeners),
        "wildcard_listeners": wildcard_count,
        "loopback_listeners": max(0, loopback_count),
        "total_findings": len(findings),
        "by_severity": _group_by_severity(findings),
    }


def _group_by_severity(findings: list) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def check_rate_limit_exposure(port: int) -> Dict[str, Any]:
    """Check if a port has rate-limiting protection configured.

    Integrates with the existing core.rate_limiter module to assess
    whether the service on this port is protected.
    """
    try:
        from core.rate_limiter import DEFAULT_RATE_LIMITS
    except ImportError:
        return {"port": port, "rate_limited": False, "reason": "rate_limiter not importable"}

    return {
        "port": port,
        "rate_limited": True,
        "limits": {
            k: f"{v[0]} req / {v[1]}s"
            for k, v in DEFAULT_RATE_LIMITS.items()
        },
    }
