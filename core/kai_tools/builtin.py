"""Built-in KAI tools — thin wrappers over existing verified orchestrator
functions. Nothing here reimplements infrastructure (JARVIS §2/§77).

All initial tools are SAFE (read-only inspection) except two CONTROLLED ones
(docker restart, service restart via existing restarter) — those prove the
policy gate end-to-end. HIGH_RISK tools get added when real destructive
operations need wrapping; the class exists and is enforced from day one.
"""

from __future__ import annotations

import json

from core.kai_tools.registry import SAFE, CONTROLLED, ToolSpec, tool


def _read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


# --- kai.system.* : health + inventory --------------------------------------

@tool(ToolSpec(
    id="kai.system.health", name="System health",
    description="KAI self-diagnostics: scheduler heartbeat, providers, circuit breakers, disk/mem of this host.",
    risk=SAFE, tags=["system", "diagnostics"]))
def system_health() -> dict:
    import shutil
    from core.memory import load
    hb = _read_json("/var/lib/ai-orchestrator/heartbeat", None)
    usage = shutil.disk_usage("/")
    state = {
        "scheduler_heartbeat": hb,
        "disk_percent": round(usage.used / usage.total * 100, 1),
        "circuit_breakers": len(load("circuit_breaker.json", {}) or {}),
        "provider_state": len(load("provider_state.json", {}) or {}),
    }
    return state


@tool(ToolSpec(
    id="kai.server.inspect", name="Inspect servers",
    description="Infrastructure inventory: last scan of host/docker/proxmox entities.",
    risk=SAFE, tags=["infrastructure"]))
def server_inspect() -> dict:
    from core.memory import MEMORY_DIR
    scan = _read_json(MEMORY_DIR / "last_scan.json", None)
    if not scan:
        try:
            from core.scanner import scan
            scan()
            scan = _read_json(MEMORY_DIR / "last_scan.json", {})
        except Exception as e:
            return {"error": f"scan unavailable: {e}"}
    # keep the payload bounded — summary counts, full detail on request
    out = {"scanned_at": scan.get("ts") or scan.get("scanned_at")}
    for key in ("docker", "containers"):
        v = scan.get(key)
        if isinstance(v, list):
            out[key] = {"count": len(v), "names": [c.get("name") for c in v[:20] if isinstance(c, dict)]}
    for key in ("proxmox", "nodes"):
        v = scan.get(key)
        if isinstance(v, list):
            out[key] = {"count": len(v)}
        elif isinstance(v, dict):
            out[key] = {k: (len(x) if isinstance(x, list) else x) for k, x in list(v.items())[:8]}
    return out


@tool(ToolSpec(
    id="kai.server.proxmox_status", name="Proxmox status",
    description="Live Proxmox nodes + guest list from the proxmox registry.",
    risk=SAFE, tags=["infrastructure", "proxmox"]))
def proxmox_status(node: str | None = None) -> dict:
    try:
        from core.proxmox_registry import discover_all_inventory
        inv = discover_all_inventory()
        if node:
            nodes = [n for n in (inv.get("nodes") or []) if n.get("node") == node]
            return {"nodes": nodes}
        return {"node_count": len(inv.get("nodes") or []),
                "guest_count": len(inv.get("guests") or []),
                "nodes": [{"node": n.get("node"), "status": n.get("status")}
                          for n in (inv.get("nodes") or [])][:10]}
    except Exception as e:
        return {"error": f"proxmox registry unavailable: {e}"}


# --- kai.workers.* : workforce ----------------------------------------------

@tool(ToolSpec(
    id="kai.workers.list", name="List workers",
    description="AI workforce registry: workers, kinds, statuses.",
    risk=SAFE, tags=["workforce"]))
def workers_list(kind: str | None = None) -> dict:
    from core.workforce import registry
    rows = registry.list_workers(kind=kind)
    data = [r.__dict__ if hasattr(r, "__dict__") else r for r in rows]
    return {"count": len(data), "workers": [
        {k: w.get(k) if isinstance(w, dict) else getattr(w, k, None)
         for k in ("worker_id", "kind", "status", "provider", "model")}
        for w in data[:50]]}


# --- kai.costs.* -------------------------------------------------------------

@tool(ToolSpec(
    id="kai.costs.summary", name="Cost summary",
    description="AI spend: totals by day/provider for a lookback window.",
    risk=SAFE, tags=["costs"]))
def costs_summary(days: int = 7) -> dict:
    from core.ai.cost_tracker import get_cost_summary
    s = get_cost_summary(days=min(max(days, 1), 90))
    # trim to essentials
    return {k: s.get(k) for k in ("total_cost", "total_calls", "by_provider", "daily") if k in s}


# --- kai.alerts.* ------------------------------------------------------------

@tool(ToolSpec(
    id="kai.alerts.pending_approvals", name="Pending approvals",
    description="Approval queue: actions awaiting the operator.",
    risk=SAFE, tags=["approvals"]))
def pending_approvals() -> dict:
    from core import approval
    rows = approval.list_pending()
    return {"count": len(rows), "pending": rows[:20]}


@tool(ToolSpec(
    id="kai.notifications.recent", name="Recent notifications",
    description="Recent orchestrator notifications with severity.",
    risk=SAFE, tags=["notifications"]))
def notifications_recent(limit: int = 15) -> dict:
    from core.memory import MEMORY_DIR
    rows = _read_json(MEMORY_DIR / "notifications.json", [])
    if not isinstance(rows, list):
        rows = rows.get("notifications", []) if isinstance(rows, dict) else []
    rows = sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)
    rows = rows[:min(max(limit, 1), 100)]
    return {"count": len(rows), "notifications": [
        {k: r.get(k) for k in ("severity", "title", "source", "created_at") if k in r}
        for r in rows]}


# --- CONTROLLED examples (prove the policy gate) ------------------------------

@tool(ToolSpec(
    id="kai.docker.container_action", name="Docker container action",
    description="Restart/stop/start a docker container on this host. CONTROLLED: policy-gated.",
    risk=CONTROLLED,
    inputs={"container": "str", "action": "str"}, timeout_s=60.0,
    tags=["infrastructure", "docker"]))
def docker_container_action(container: str, action: str) -> dict:
    if action not in ("restart", "start", "stop"):
        raise ValueError(f"unsupported action '{action}' — restart|start|stop only")
    import subprocess
    r = subprocess.run(["docker", action, container], capture_output=True, text=True, timeout=55)
    return {"container": container, "action": action, "rc": r.returncode,
            "output": (r.stdout or r.stderr).strip()[-400:]}


@tool(ToolSpec(
    id="kai.service.restart", name="Restart systemd service",
    description="Restart a systemd unit on this host. CONTROLLED: policy-gated.",
    risk=CONTROLLED,
    inputs={"unit": "str"}, timeout_s=60.0,
    tags=["infrastructure", "systemd"]))
def service_restart(unit: str) -> dict:
    allowed_prefixes = ("kai-", "ai-orchestrator")
    if not unit.startswith(allowed_prefixes):
        raise ValueError(f"unit '{unit}' outside allowed prefixes {allowed_prefixes}")
    import subprocess
    r = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True, timeout=55)
    return {"unit": unit, "rc": r.returncode, "output": (r.stderr or "").strip()[-300:]}
