"""Built-in KAI tools — thin wrappers over existing verified orchestrator
functions. Nothing here reimplements infrastructure (JARVIS §2/§77).

All initial tools are SAFE (read-only inspection) except two CONTROLLED ones
(docker restart, service restart via existing restarter) — those prove the
policy gate end-to-end. HIGH_RISK tools get added when real destructive
operations need wrapping; the class exists and is enforced from day one.
"""

from __future__ import annotations

import base64
import json

from core.kai_tools.registry import SAFE, CONTROLLED, HIGH_RISK, ToolSpec, tool


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


# --- kai.world.* : JARVIS P4 world model -------------------------------------

@tool(ToolSpec(
    id="kai.world.state", name="World state",
    description="World Model summary: entity counts by type + recent changes.",
    risk=SAFE, tags=["world"]))
def world_state() -> dict:
    from core.world_model import get_state
    return get_state()


@tool(ToolSpec(
    id="kai.world.refresh", name="Refresh world model",
    description="Re-collect live entities from proxmox/docker/workforce and diff vs previous snapshot.",
    risk=SAFE, timeout_s=120.0, tags=["world"]))
def world_refresh() -> dict:
    from core import world_model
    snap = world_model.build_snapshot()
    return {"updated_at": snap.get("updated_at"), **(snap.get("counts") or {}),
            "changes": snap.get("changes_since_previous", [])[:20]}


@tool(ToolSpec(
    id="kai.world.impact", name="Failure impact analysis",
    description="If a component fails, what transitively depends on it? Dependency-graph traversal.",
    risk=SAFE, tags=["world"],
    inputs={"entity_id": "str e.g. ct:104 | svc:npm-ct104 | host:pve-b"}))
def world_impact(entity_id: str) -> dict:
    from core.world_model import impact_of
    return impact_of(entity_id)


# --- kai.executive.* : JARVIS P10 ---------------------------------------------

@tool(ToolSpec(
    id="kai.executive.prioritize", name="What matters now",
    description="Executive prioritization: critical / needs-attention / watch, aggregated from world+costs+approvals.",
    risk=SAFE, tags=["executive"]))
def executive_prioritize() -> dict:
    from core.kai_executive import prioritize
    return prioritize()


@tool(ToolSpec(
    id="kai.executive.briefing", name="Generate briefing",
    description="Executive briefing (facts only) — optionally delivered to Telegram.",
    risk=SAFE, timeout_s=60.0, tags=["executive"],
    inputs={"kind": "auto|morning|evening|infrastructure|security", "send": "bool"}))
def executive_briefing(kind: str = "auto", send: bool = False) -> dict:
    from core.kai_executive import run_briefing
    return {"text": run_briefing(kind=kind, send=bool(send))}


@tool(ToolSpec(
    id="kai.memory.remember_decision", name="Record decision",
    description="Store a structured decision record (what/why/alternatives).",
    risk=CONTROLLED, tags=["memory"],
    inputs={"decision": "str", "reason": "str", "alternatives": "list?"}))
def remember_decision(decision: str, reason: str, alternatives=None) -> dict:
    from core.kai_executive import remember_decision as rd
    rec = rd(decision, reason, alternatives=alternatives)
    return {"stored": True, "ts": rec.get("ts")}


@tool(ToolSpec(
    id="kai.memory.failures", name="Failure memory",
    description="Recent failure records; verified_only filters to confirmed lessons.",
    risk=SAFE, tags=["memory"],
    inputs={"verified_only": "bool"}))
def failure_memory(verified_only: bool = False) -> dict:
    from core.kai_executive import recent_failures
    rows = recent_failures(limit=15, verified_only=verified_only)
    return {"count": len(rows), "failures": rows}


# --- kai.proactive.* : JARVIS P13 ---------------------------------------------

@tool(ToolSpec(
    id="kai.proactive.run", name="Proactive check",
    description="Observe → predict cycle: trend-based predictions w/ confidence, world-model detections.",
    risk=SAFE, timeout_s=90.0, tags=["proactive"]))
def proactive_run() -> dict:
    from core.kai_proactive import run_cycle
    return run_cycle(notify_threshold="warn")


@tool(ToolSpec(
    id="kai.proactive.predictions", name="Prediction history",
    description="Recent predictions/detections from the proactive engine.",
    risk=SAFE, tags=["proactive"]))
def proactive_history() -> dict:
    import json
    try:
        with open("/project/ai-orchestrator/memory/kai_predictions.json") as fh:
            rows = json.load(fh).get("records", [])
        return {"count": len(rows), "predictions": rows[-15:][::-1]}
    except FileNotFoundError:
        return {"count": 0, "predictions": []}


# --- kai.missions.* : JARVIS P11 ------------------------------------------------

@tool(ToolSpec(
    id="kai.missions.create", name="Create mission",
    description="Plan a mission: ordered tool tasks, executed through the policy gate. Missions never spawn missions.",
    risk=CONTROLLED, timeout_s=60.0, tags=["missions"],
    inputs={"objective": "str", "tasks": "list[{tool_id,args?,description?}]", "goal_id": "str?"}))
def mission_create(objective: str, tasks: list, goal_id: str | None = None) -> dict:
    from core.kai_missions import create_mission
    m = create_mission(objective, tasks, goal_id=goal_id)
    return {"id": m["id"], "status": m["status"], "tasks": len(m["tasks"])}


@tool(ToolSpec(
    id="kai.missions.execute", name="Execute mission",
    description="Run a planned mission's tasks sequentially through the policy gate. Stops on failure/blocked.",
    risk=CONTROLLED, timeout_s=300.0, tags=["missions"],
    inputs={"mission_id": "str"}))
def mission_execute(mission_id: str) -> dict:
    from core.kai_missions import execute_mission
    return execute_mission(mission_id)


@tool(ToolSpec(
    id="kai.missions.list", name="List missions",
    description="All missions with status + progress.",
    risk=SAFE, tags=["missions"]))
def mission_list(status: str | None = None) -> dict:
    from core.kai_missions import list_missions
    rows = list_missions(status)
    return {"count": len(rows), "missions": rows}


@tool(ToolSpec(
    id="kai.missions.cancel", name="Cancel mission",
    description="Cancel a running/planned mission; pending tasks become blocked.",
    risk=CONTROLLED, tags=["missions"],
    inputs={"mission_id": "str", "reason": "str"}))
def mission_cancel(mission_id: str, reason: str = "operator requested") -> dict:
    from core.kai_missions import cancel_mission
    m = cancel_mission(mission_id, reason)
    return {"id": m["id"], "status": m["status"]}


@tool(ToolSpec(
    id="kai.goals.list", name="List goals",
    description="Active goals with computed progress from their missions.",
    risk=SAFE, tags=["missions"]))
def goals_list() -> dict:
    from core.kai_missions import list_goals
    rows = list_goals()
    return {"count": len(rows), "goals": rows}


# --- kai.browser.* + kai.vision.* : JARVIS P7/P9 --------------------------------

BROWSER_URL = "http://192.168.1.120:8140"


def _browser_post(path: str, payload: dict, timeout_s: float = 45.0) -> dict:
    import requests
    r = requests.post(f"{BROWSER_URL}{path}", json=payload, timeout=timeout_s)
    if r.status_code != 200:
        err = r.json().get("detail", r.status_code)
        raise RuntimeError(f"browser {path}: {err}")
    return r.json()


@tool(ToolSpec(
    id="kai.browser.navigate", name="Browse page",
    description="Navigate to a URL in the sandboxed headless browser and extract text.",
    risk=SAFE, timeout_s=60.0, tags=["browser"],
    inputs={"url": "str", "session": "str? (named sessions keep cookies)"}))
def browser_navigate(url: str, session: str | None = None) -> dict:
    return _browser_post("/navigate", {"url": url, "session": session})


@tool(ToolSpec(
    id="kai.browser.act", name="Browser actions",
    description="Click/type/press steps on a page in a NAMED session (stateful cookies).",
    risk=CONTROLLED, timeout_s=90.0, tags=["browser"],
    inputs={"session": "str", "url": "str?", "steps": "list"}))
def browser_act(session: str, url: str | None = None, steps: list | None = None) -> dict:
    return _browser_post("/act", {"session": session, "url": url,
                                  "steps": steps or []}, timeout_s=80.0)


@tool(ToolSpec(
    id="kai.vision.analyze_url", name="Look at webpage",
    description="Screenshot a URL and analyze it with the vision model — 'what is wrong with this page?'",
    risk=SAFE, timeout_s=120.0, tags=["vision", "browser"],
    inputs={"url": "str", "question": "str?"}))
def vision_analyze_url(url: str, question: str = "Describe this page and note anything unusual.") -> dict:
    shot = _browser_post("/screenshot", {"url": url}, timeout_s=60.0)
    png = base64.b64decode(shot["png_base64"])
    return {"url": url, **_vision_ask(png, question), "screenshot_bytes": len(png)}


def _vision_ask(png_bytes: bytes, question: str) -> dict:
    """Vision via Gemini native generateContent with inline_data (multimodal
    part array). Falls back to a text-only description error honestly."""
    import os as _os
    key = _os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            from core.ai.secrets import get_api_key
            key = get_api_key("gemini") or ""
        except Exception:
            pass
    if not key:
        raise RuntimeError("no vision provider configured (GEMINI_API_KEY missing)")
    import requests as _rq
    from core.ai.secrets import get_api_key as _gak
    model = "gemini-flash-lite-latest"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [
        {"text": question},
        {"inline_data": {"mime_type": "image/png",
                         "data": base64.b64encode(png_bytes).decode()}},
    ]}]}
    r = _rq.post(url, params={"key": key}, json=body, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"vision provider {r.status_code}: {r.text[:150]}")
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return {"analysis": str(text)[:3000], "provider": f"gemini:{model}"}


# --- kai.twin.* (P14) + kai.workers.delegate (P12) --------------------------------

@tool(ToolSpec(
    id="kai.twin.simulate", name="Simulate change",
    description="Digital twin: project the impact of fail/remove/restart/scale on an entity WITHOUT touching production.",
    risk=SAFE, timeout_s=60.0, tags=["twin", "simulation"],
    inputs={"entity_id": "str", "scenario": "fail|remove|restart|scale_up|scale_down"}))
def twin_simulate(entity_id: str, scenario: str = "fail") -> dict:
    from core.kai_twin import simulate
    return simulate(entity_id, scenario)


@tool(ToolSpec(
    id="kai.twin.scenarios", name="List scenarios",
    description="What-if options available for an entity.",
    risk=SAFE, tags=["twin"]))
def twin_scenarios(entity_id: str) -> dict:
    from core.kai_twin import scenarios_for
    return scenarios_for(entity_id)


@tool(ToolSpec(
    id="kai.workers.delegate", name="Delegate reasoning",
    description="Send a reasoning/analysis task through the existing performance-weighted provider router.",
    risk=SAFE, timeout_s=120.0, tags=["workforce", "ai"],
    inputs={"task": "str", "task_type": "planning|review|classification|documentation"}))
def workers_delegate(task: str, task_type: str = "planning") -> dict:
    if len(task) > 8000:
        raise ValueError("task text too long (max 8000 chars)")
    from core.ai.ai_router import delegate as router_delegate
    result = router_delegate(task, task_type=task_type if task_type in (
        "planning", "review", "classification", "documentation") else "planning",
        timeout=100)
    response = result.get("response", result)
    return {"response": str(response)[:6000],
            "provider": result.get("provider") or result.get("provider_used")}


# --- kai.selfimprove.* : JARVIS P21 ----------------------------------------------
# Controlled self-improvement (§32): KAI proposes → human reviews → existing
# build pipeline executes in sandbox → verify → deploy. KAI never edits its
# own production code directly; the build pipeline's approval gates + rollback
# are the enforcement mechanism.

@tool(ToolSpec(
    id="kai.selfimprove.propose", name="Propose improvement",
    description="File a self-improvement proposal for operator review. No code changes happen until approved through a build.",
    risk=CONTROLLED, tags=["self-improvement"],
    inputs={"title": "str", "rationale": "str", "change_summary": "str"}))
def selfimprove_propose(title: str, rationale: str, change_summary: str) -> dict:
    from core.kai.planner import save_proposals, load_proposals
    import uuid
    proposals = load_proposals()
    prop = {
        "id": f"prop-{uuid.uuid4().hex[:8]}",
        "title": title[:200],
        "rationale": rationale[:2000],
        "change_summary": change_summary[:2000],
        "status": "proposed",
        "source": "kai-self-improvement",
        "created_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    }
    proposals.append(prop)
    save_proposals(proposals)
    return {"id": prop["id"], "status": prop["status"],
            "note": "awaiting review — nothing changes until approved and built"}


@tool(ToolSpec(
    id="kai.selfimprove.proposals", name="List improvement proposals",
    description="Self-improvement proposals and their review status.",
    risk=SAFE, tags=["self-improvement"]))
def selfimprove_list(status: str | None = None) -> dict:
    from core.kai.planner import list_proposals
    rows = [p for p in list_proposals() if isinstance(p, dict)]
    rows = [p for p in rows if p.get("source") == "kai-self-improvement"]
    if status:
        rows = [p for p in rows if p.get("status") == status]
    return {"count": len(rows), "proposals": rows[-20:][::-1]}


@tool(ToolSpec(
    id="kai.emergency.stop", name="EMERGENCY STOP",
    description="Stop everything: pause scheduler, block all tool execution, cancel running missions.",
    risk=HIGH_RISK, timeout_s=60.0, tags=["emergency", "security"]))
def emergency_stop(reason: str = "operator requested") -> dict:
    # Even though HIGH_RISK forces approval, once approved this runs.
    from core.kai_emergency import emergency_stop as stop
    return stop(operator="operator", reason=reason)


@tool(ToolSpec(
    id="kai.emergency.resume", name="Resume after stop",
    description="Clear emergency stop: re-enable tools + scheduler.",
    risk=CONTROLLED, timeout_s=30.0, tags=["emergency"]))
def emergency_resume() -> dict:
    from core.kai_emergency import emergency_resume as resume
    return resume()
