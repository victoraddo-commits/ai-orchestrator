"""Phase 17F: Multi-node Proxmox continuous monitoring.

Extends the existing health/incident/remediation pipeline to cover both
Proxmox A (192.168.99.2) and Proxmox B (via WireGuard).  Detects node
availability, LXC/VM health, storage usage, and backup status.

TK-176d6efe: VPN failover — each node now supports a fallback_host (tried
when the primary is unreachable) and retry with exponential backoff on
initial connection failure.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests


def _get_verify():
    """Return the CA cert path for TLS verification, or False to disable it."""
    ca_cert = os.getenv("PROXMOX_CA_CERT", "")
    return ca_cert if ca_cert else False


PROXMOX_NODES = [
    {
        "name": "pve",
        "host": os.environ.get("PROXMOX_HOST", "192.168.99.2"),
        # TK-176d6efe: LAN is primary, no fallback needed — but if Proxmox A
        # ever goes remote, set PROXMOX_FALLBACK_HOST in the environment.
        "fallback_host": os.environ.get("PROXMOX_FALLBACK_HOST", ""),
        "token": os.environ.get("PROXMOX_TOKEN", ""),
    },
    {
        "name": "pve-b",
        "host": os.environ.get("PROXMOX_B_HOST", "192.168.1.109"),
        # Proxmox B is reachable via LAN (192.168.1.109). Set
        # PROXMOX_B_FALLBACK_HOST to enable a secondary path.
        "fallback_host": os.environ.get("PROXMOX_B_FALLBACK_HOST", ""),
        "token_id": os.environ.get("PROXMOX_B_TOKEN_ID", "kai@pve!kai"),
        "token_secret": os.environ.get("PROXMOX_B_TOKEN_SECRET", ""),
    },
]
PROXMOX_NODES = [n for n in PROXMOX_NODES if n.get("token") or n.get("token_secret")]

# TK-176d6efe: retry constants
_MAX_RETRIES = int(os.environ.get("PROXMOX_RETRY_COUNT", "3"))
_RETRY_BASE_DELAY = float(os.environ.get("PROXMOX_RETRY_BASE_SECONDS", "1.5"))
_REQUEST_TIMEOUT = int(os.environ.get("PROXMOX_REQUEST_TIMEOUT", "15"))

# TK-176d6efe: per-node VPN health cache (shared across collect_all_nodes calls)
_vpn_status_cache: dict[str, dict] = {}


def _build_headers(node):
    """Build Proxmox API auth headers for a node config."""
    token = node.get("token", "")
    if token:
        return {"Authorization": f"PVEAPIToken={token}"}
    tid = node.get("token_id", "")
    tsec = node.get("token_secret", "")
    return {"Authorization": f"PVEAPIToken={tid}={tsec}"}


def _do_request(host, headers, path, timeout=_REQUEST_TIMEOUT):
    """Single request attempt.  Returns (data_dict | None, error_type | None).

    error_type is one of: "connection" (network unreachable), "auth" (401/403),
    or None (success).
    """
    try:
        resp = requests.get(
            f"https://{host}:8006/api2/json/{path}",
            headers=headers, timeout=timeout, verify=_get_verify(),
        )
        if resp.status_code == 200:
            return resp.json().get("data", {}), None
        if resp.status_code in (401, 403):
            return None, "auth"
        return None, "connection"
    except Exception:
        return None, "connection"


def _api_get(node, path):
    """Fetch *path* from *node*, trying primary → fallback with retries.

    TK-176d6efe: each host is tried up to _MAX_RETRIES times with
    exponential backoff.  If the primary fails all retries and a
    fallback_host is configured, the fallback gets the same treatment.
    """

    headers = _build_headers(node)
    hosts_to_try = [node["host"]]

    fallback = node.get("fallback_host", "")
    if fallback and fallback != node["host"]:
        hosts_to_try.append(fallback)

    last_error = None
    last_error_type = None

    for host in hosts_to_try:
        for attempt in range(1, _MAX_RETRIES + 1):
            result, error_type = _do_request(host, headers, path)
            if result is not None:
                # Update VPN status cache for this node
                _vpn_status_cache[node["name"]] = {
                    "host_used": host,
                    "reachable": True,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "attempts": attempt,
                }
                return result

            last_error_type = error_type
            last_error = f"{error_type or 'no response'} from {host} after {attempt} attempt(s)"
            if attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)

        # Mark this host as failed in cache before trying fallback.
        # Distinguish auth failures (host is reachable) from connection failures.
        cache_entry: dict = {
            "host_used": host,
            "reachable": last_error_type == "auth",  # auth failure = host IS reachable
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "attempts": _MAX_RETRIES,
            "error": f"{last_error_type or 'connection'}_failure",
        }
        _vpn_status_cache[node["name"]] = cache_entry

    return None


def get_vpn_status(node_name=None):
    """TK-176d6efe: Return cached VPN/tunnel status for one or all nodes.

    Returns: dict node_name → {reachable, host_used, checked_at, attempts, error?}
    """
    if node_name:
        v = _vpn_status_cache.get(node_name)
        return {node_name: v} if v else {}
    return dict(_vpn_status_cache)


def collect_node_health(node):
    h = {"node": node["name"], "host": node["host"], "reachable": False,
         "checked_at": datetime.now(timezone.utc).isoformat()}

    # TK-176d6efe: include fallback info
    if node.get("fallback_host"):
        h["fallback_host"] = node["fallback_host"]

    node_info = _api_get(node, "nodes")
    if not node_info:
        vpn = _vpn_status_cache.get(node["name"])
        error_kind = vpn.get("error", "unreachable") if vpn else "unreachable"
        h["error"] = error_kind
        # TK-176d6efe: merge VPN/tunnel status into health record
        vpn = _vpn_status_cache.get(node["name"])
        if vpn:
            h["vpn_status"] = vpn
        return h
    h["reachable"] = True

    status = _api_get(node, f"nodes/{node['name']}/status")
    if status:
        h["uptime"] = status.get("uptime", 0)
        h["cpu"] = round(status.get("cpu", 0) * 100, 1)
        h["memory_used"] = status.get("mem", 0)
        h["memory_total"] = status.get("maxmem", 1)
        h["memory_pct"] = round(h["memory_used"] / max(h["memory_total"], 1) * 100, 1)

    containers = _api_get(node, f"nodes/{node['name']}/lxc") or []
    vms = _api_get(node, f"nodes/{node['name']}/qemu") or []
    h["containers"] = len(containers)
    h["vms"] = len(vms)
    h["running_containers"] = len([c for c in containers if c.get("status") == "running"])
    h["running_vms"] = len([v for v in vms if v.get("status") == "running"])

    storage = _api_get(node, f"nodes/{node['name']}/storage") or []
    storages = []
    for s in storage:
        storages.append({
            "name": s.get("storage", "?"), "type": s.get("type", "?"),
            "used_bytes": s.get("used", 0), "total_bytes": s.get("total", 0),
            "used_pct": round(s.get("used", 0) / max(s.get("total", 1), 1) * 100, 1),
        })
    h["storage"] = storages

    backups = _api_get(node, f"nodes/{node['name']}/storage/local/backup") or []
    recent = []
    for b in backups[-5:]:
        recent.append({
            "volid": b.get("volid", "?")[:50], "size": b.get("size", 0),
            "ctime": datetime.fromtimestamp(b.get("ctime", 0), tz=timezone.utc).isoformat() if b.get("ctime") else "?",
        })
    h["recent_backups"] = recent
    return h


def collect_all_nodes():
    return {n["name"]: collect_node_health(n) for n in PROXMOX_NODES}


def check_alerts(health_data):
    alerts = []
    for name, h in health_data.items():
        if not h.get("reachable"):
            error_msg = h.get("error", "unreachable")
            # Auth failures mean the host IS reachable — don't report as
            # VPN/critical alert; it's an auth config issue.
            severity = "warning" if "auth" in str(error_msg) else "critical"
            alerts.append({"node": name, "severity": severity, "component": "proxmox",
                           "message": f"Proxmox {name} ({h.get('host','?')}): {error_msg}"})
            continue
        for s in h.get("storage", []):
            if s.get("used_pct", 0) > 90:
                alerts.append({"node": name, "severity": "warning", "component": "storage",
                               "message": f"Storage {s['name']} on {name} at {s['used_pct']}%"})
        if h.get("memory_pct", 0) > 90:
            alerts.append({"node": name, "severity": "warning", "component": "memory",
                           "message": f"Memory on {name} at {h['memory_pct']}%"})
    return alerts
