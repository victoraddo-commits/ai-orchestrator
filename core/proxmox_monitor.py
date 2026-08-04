"""Phase 17F: Multi-node Proxmox continuous monitoring.

Extends the existing health/incident/remediation pipeline to cover both
Proxmox A (192.168.99.2) and Proxmox B (via WireGuard).  Detects node
availability, LXC/VM health, storage usage, and backup status.
"""

import json
import os
from datetime import datetime, timezone

import requests

PROXMOX_NODES = [
    {
        "name": "pve",
        "host": os.environ.get("PROXMOX_HOST", "192.168.99.2"),
        "token": os.environ.get("PROXMOX_TOKEN", ""),
    },
    {
        "name": "pve-b",
        "host": os.environ.get("PROXMOX_B_HOST", "10.8.0.102"),
        "token_id": os.environ.get("PROXMOX_B_TOKEN_ID", "kai@pve!kai"),
        "token_secret": os.environ.get("PROXMOX_B_TOKEN_SECRET", ""),
    },
]
PROXMOX_NODES = [n for n in PROXMOX_NODES if n.get("token") or n.get("token_secret")]


def _api_get(node, path):
    host = node["host"]
    token = node.get("token", "")
    if token:
        headers = {"Authorization": f"PVEAPIToken={token}"}
    else:
        tid = node.get("token_id", "")
        tsec = node.get("token_secret", "")
        headers = {"Authorization": f"PVEAPIToken={tid}={tsec}"}

    try:
        resp = requests.get(
            f"https://{host}:8006/api2/json/{path}",
            headers=headers, timeout=15, verify=False,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {})
    except Exception:
        pass
    return None


def collect_node_health(node):
    h = {"node": node["name"], "host": node["host"], "reachable": False,
         "checked_at": datetime.now(timezone.utc).isoformat()}

    node_info = _api_get(node, "nodes")
    if not node_info:
        h["error"] = "unreachable"
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
            alerts.append({"node": name, "severity": "critical", "component": "proxmox",
                           "message": f"Proxmox {name} ({h.get('host','?')}) unreachable"})
            continue
        for s in h.get("storage", []):
            if s.get("used_pct", 0) > 90:
                alerts.append({"node": name, "severity": "warning", "component": "storage",
                               "message": f"Storage {s['name']} on {name} at {s['used_pct']}%"})
        if h.get("memory_pct", 0) > 90:
            alerts.append({"node": name, "severity": "warning", "component": "memory",
                           "message": f"Memory on {name} at {h['memory_pct']}%"})
    return alerts
