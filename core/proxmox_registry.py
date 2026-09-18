"""Phase 17E: Multi-node Proxmox registry — full inventory discovery
for Proxmox A + B, extensible to additional nodes.

Discovers all LXC containers, VMs, storage pools, networks, and backups
across configured Proxmox nodes.
"""

from datetime import datetime, timezone
import socket
import threading
import time

from core.proxmox_monitor import _get_node_configs, _api_get


def _tcp_reachable(host, default_port: int = 8006, timeout: float = 5.0) -> bool:
    """True if a TCP connection to ``host[:port]`` succeeds."""
    if not host:
        return False
    h, _, p = str(host).partition(":")
    port = int(p) if p else default_port
    try:
        with socket.create_connection((h, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_node_inventory(node):
    """Full inventory for one Proxmox node."""
    inv = {
        "node": node["name"],
        "host": node["host"],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "reachable": False,
    }

    node_info = _api_get(node, "nodes")
    if not node_info:
        # A node can be UP while the API call fails (e.g. a stale API token
        # returns 401). Never report a live node as "down": fall back to a TCP
        # reachability probe and mark the guest inventory as unavailable.
        if _tcp_reachable(node.get("host")):
            inv["reachable"] = True
            inv["api_unavailable"] = True
            return inv
        inv["error"] = "unreachable"
        return inv
    inv["reachable"] = True

    # The PVE nodename is NOT necessarily our friendly alias (e.g. alias
    # 'pve-b' vs actual nodename 'pve') — resolve the real one from the API,
    # preferring an online node.  JARVIS P4 fix 2026-08-24: B-side inventory
    # was silently empty because we queried nodes/pve-b/... (404 → []).
    pve_node_name = None
    if isinstance(node_info, list) and node_info:
        online = [n for n in node_info if n.get("status") == "online"]
        pve_node_name = (online or node_info)[0].get("node")
    pve_node_name = pve_node_name or node["name"]

    # Containers
    containers = _api_get(node, f"nodes/{pve_node_name}/lxc") or []
    inv["containers"] = []
    for c in containers:
        inv["containers"].append({
            "vmid": c.get("vmid"), "name": c.get("name", "?"),
            "status": c.get("status", "?"),
            "cpu": c.get("cpus"), "memory_mb": c.get("maxmem", 0) // (1024 * 1024),
            "disk_gb": round(c.get("maxdisk", 0) / (1024 * 1024 * 1024), 1),
            "template": c.get("template", False),
        })

    # VMs
    vms = _api_get(node, f"nodes/{pve_node_name}/qemu") or []
    inv["vms"] = []
    for v in vms:
        inv["vms"].append({
            "vmid": v.get("vmid"), "name": v.get("name", "?"),
            "status": v.get("status", "?"),
            "cpu": v.get("cpus"), "memory_mb": v.get("maxmem", 0) // (1024 * 1024),
            "disk_gb": round(v.get("maxdisk", 0) / (1024 * 1024 * 1024), 1),
        })

    # Storage
    storage = _api_get(node, f"nodes/{pve_node_name}/storage") or []
    inv["storage"] = []
    for s in storage:
        inv["storage"].append({
            "name": s.get("storage"), "type": s.get("type"),
            "total_gb": round(s.get("total", 0) / (1024 * 1024 * 1024), 1),
            "used_gb": round(s.get("used", 0) / (1024 * 1024 * 1024), 1),
            "avail_gb": round(s.get("avail", 0) / (1024 * 1024 * 1024), 1),
        })

    # Network interfaces
    network = _api_get(node, f"nodes/{pve_node_name}/network") or []
    inv["network"] = []
    for n in network:
        inv["network"].append({
            "iface": n.get("iface"), "type": n.get("type"),
            "active": n.get("active", False), "address": n.get("address", ""),
        })

    # Backups
    backups = _api_get(node, f"nodes/{pve_node_name}/storage/local/backup") or []
    inv["backups"] = []
    for b in backups:
        inv["backups"].append({
            "volid": b.get("volid", "")[:60], "size_gb": round(b.get("size", 0) / (1024 * 1024 * 1024), 1),
            "vmid": b.get("vmid"), "type": b.get("content", "?"),
        })

    return inv


def discover_all_inventory():
    """Full multi-node inventory."""
    return [discover_node_inventory(n) for n in _get_node_configs()]


def get_registry_summary():
    """Summary for the dashboard — served from a background-refreshed cache.

    Full discovery can block for a long time when a Proxmox node's API is
    unreachable (network timeouts). Returning the last snapshot immediately
    and refreshing in a daemon thread keeps the Command Center responsive.
    """
    with _CACHE_LOCK:
        data = _CACHE["data"]
        stale = (time.time() - _CACHE["at"]) > _CACHE_TTL
    if data is None or stale:
        _start_refresh()
    if data is None:
        return _empty_summary()
    return data


def _build_summary():
    inv = discover_all_inventory()
    total_ct = sum(len(n.get("containers", [])) for n in inv)
    total_vm = sum(len(n.get("vms", [])) for n in inv)
    running_ct = sum(len([c for c in n.get("containers", []) if c.get("status") == "running"]) for n in inv)
    running_vm = sum(len([v for v in n.get("vms", []) if v.get("status") == "running"]) for n in inv)
    total_disk_gb = sum(sum(s.get("total_gb", 0) for s in n.get("storage", [])) for n in inv)
    used_disk_gb = sum(sum(s.get("used_gb", 0) for s in n.get("storage", [])) for n in inv)

    return {
        "nodes": len(inv),
        "reachable": sum(1 for n in inv if n.get("reachable")),
        "containers": total_ct, "running_containers": running_ct,
        "vms": total_vm, "running_vms": running_vm,
        "storage_total_gb": round(total_disk_gb, 1), "storage_used_gb": round(used_disk_gb, 1),
        "inventory": inv,
        "cached": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _empty_summary():
    return {"nodes": 0, "reachable": 0, "containers": 0, "running_containers": 0,
            "vms": 0, "running_vms": 0, "storage_total_gb": 0.0,
            "storage_used_gb": 0.0, "inventory": [], "cached": False,
            "discovering": True}


_CACHE = {"data": None, "at": 0.0}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300.0
_REFRESH_LOCK = threading.Lock()
_REFRESHING = {"busy": False}


def _refresh_cache():
    try:
        data = _build_summary()
        with _CACHE_LOCK:
            _CACHE["data"] = data
            _CACHE["at"] = time.time()
    except Exception:  # noqa: BLE001
        pass


def _start_refresh():
    with _REFRESH_LOCK:
        if _REFRESHING["busy"]:
            return
        _REFRESHING["busy"] = True
    def _run():
        try:
            _refresh_cache()
        finally:
            _REFRESHING["busy"] = False
    threading.Thread(target=_run, daemon=True).start()


# Pre-warm the inventory in the background at import so the first dashboard
# request already has a snapshot (or at least does not block on discovery).
# Skipped under pytest so the test suite never makes real network calls.
import os as _os
import sys as _sys
if "pytest" not in _sys.modules and not _os.environ.get("PYTEST_CURRENT_TEST"):
    _start_refresh()
