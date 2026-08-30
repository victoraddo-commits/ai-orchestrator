# core/connectivity_monitor.py
"""Active path testing: ping, TCP socket, HTTP health, traceroute.

Tests connectivity between Site A and Site B across the Tailscale tunnel.
"""

import subprocess
import re
from datetime import datetime, timezone
from typing import Optional
import requests


def test_latency(to_ip: str, count: int = 3) -> dict:
    """Ping target and return latency stats. Returns dict with avg_ms, min_ms, max_ms."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), to_ip],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout
        # Parse: rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5
        m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)", output)
        if m:
            return {"min_ms": float(m.group(1)), "avg_ms": float(m.group(2)), "max_ms": float(m.group(3))}
        return {"avg_ms": None}
    except Exception:
        return {"avg_ms": None}


def test_tcp_connect(ip: str, port: int, timeout: float = 3.0) -> bool:
    """Check TCP connectivity to ip:port using nc."""
    try:
        result = subprocess.run(
            ["nc", "-z", "-w", str(int(timeout)), ip, str(port)],
            capture_output=True, timeout=timeout + 1,
        )
        return result.returncode == 0
    except Exception:
        return False


def test_http_health(url: str, timeout: float = 5.0) -> dict:
    """HEAD request to URL, return status_code and elapsed_ms."""
    try:
        start = datetime.now(timezone.utc)
        # SSL verify disabled — internal URLs only
        resp = requests.head(url, timeout=timeout, verify=False, allow_redirects=True)
        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return {"status_code": resp.status_code, "elapsed_ms": elapsed_ms, "ok": 200 <= resp.status_code < 400}
    except requests.exceptions.Timeout:
        return {"status_code": None, "elapsed_ms": None, "ok": False, "error": "timeout"}
    except requests.exceptions.ConnectionError:
        return {"status_code": None, "elapsed_ms": None, "ok": False, "error": "connection_error"}
    except requests.exceptions.SSLError as e:
        return {"status_code": None, "elapsed_ms": None, "ok": False, "error": f"ssl_error: {e}"}
    except Exception as e:
        return {"status_code": None, "elapsed_ms": None, "ok": False, "error": str(e)}


def traceroute(target: str) -> list[dict]:
    """Run traceroute, return list of hop dicts."""
    try:
        result = subprocess.run(
            ["traceroute", "-m", "15", "-w", "2", target],
            capture_output=True, text=True, timeout=30,
        )
        hops = []
        for line in result.stdout.splitlines()[1:]:  # skip first line (target)
            m = re.match(r"\s*(\d+)\s+(.+)", line)
            if m:
                hops.append({"hop": int(m.group(1)), "response": m.group(2).strip()})
        return hops
    except Exception:
        return []


def test_site_paths(site_a: dict, site_b: dict) -> dict:
    """Test connectivity between two sites. Returns ConnectivityResult."""
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "a_to_b_direct": "UNKNOWN",
        "b_to_a_direct": "UNKNOWN",
        "a_subnet_to_b_subnet": "UNKNOWN",
        "a_to_b_latency_ms": None,
        "b_to_a_latency_ms": None,
        "packet_loss_pct": 0.0,
        "last_test": now,
    }

    # Site A tailscale IP
    a_ts = site_a.get("tailscale_ip", "")
    b_ts = site_b.get("tailscale_ip", "")
    a_gw = site_a.get("gateway", "")
    b_gw = site_b.get("gateway", "")
    a_px = site_a.get("proxmox_ip", "")
    b_px = site_b.get("proxmox_ip", "")

    # A → B direct (Tailscale peer)
    if a_ts and b_ts:
        lat = test_latency(b_ts, count=3)
        result["a_to_b_latency_ms"] = lat.get("avg_ms")
        result["a_to_b_direct"] = "PASS" if lat.get("avg_ms") is not None else "FAIL"

    # A → B via subnet route (ping remote gateway via LAN)
    if a_gw and b_gw:
        lat = test_latency(b_gw, count=3)
        result["a_subnet_to_b_subnet"] = "PASS" if lat.get("avg_ms") is not None else "FAIL"

    # B → A direct
    if b_ts and a_ts:
        lat = test_latency(a_ts, count=3)
        result["b_to_a_latency_ms"] = lat.get("avg_ms")
        result["b_to_a_direct"] = "PASS" if lat.get("avg_ms") is not None else "FAIL"

    # Proxmox API ports
    if a_px:
        result["a_proxmox_reachable"] = test_tcp_connect(a_px, 8006)
    if b_px:
        result["b_proxmox_reachable"] = test_tcp_connect(b_px, 8006)

    # SSH ports
    if a_px:
        result["a_ssh_reachable"] = test_tcp_connect(a_px, 22)
    if b_px:
        result["b_ssh_reachable"] = test_tcp_connect(b_px, 22)

    return result
