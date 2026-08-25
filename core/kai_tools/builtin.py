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


# --- kai.enhancements.* : hardware-gated optional capabilities -------------------

@tool(ToolSpec(
    id="kai.enhancements.status", name="Enhancement status",
    description="Optional capabilities (wake word, streaming voice, Home Assistant, telephony): enabled × requirements = state.",
    risk=SAFE, tags=["enhancements"]))
def enhancements_status() -> dict:
    from core.kai_enhancements import status
    return {"enhancements": status()}


@tool(ToolSpec(
    id="kai.enhancements.enable", name="Enable enhancement",
    description="Opt into an enhancement. Can enable BEFORE hardware exists — auto-activates when requirements are met.",
    risk=CONTROLLED, tags=["enhancements"],
    inputs={"key": "wake_word|streaming_voice|home_assistant|telephony"}))
def enhancements_enable(key: str) -> dict:
    from core.kai_enhancements import enable
    return enable(key)


@tool(ToolSpec(
    id="kai.enhancements.disable", name="Disable enhancement",
    description="Turn an optional capability off.",
    risk=CONTROLLED, tags=["enhancements"],
    inputs={"key": "str"}))
def enhancements_disable(key: str) -> dict:
    from core.kai_enhancements import disable
    return disable(key)


# --- kai.home.* : Home Assistant control (§46) — gated by enhancement ----------

def _home_gate() -> None:
    from core.kai_enhancements import capability_available
    if not capability_available("kai.home.control"):
        raise RuntimeError(
            "Home Assistant integration is DISABLED/BLOCKED — enable it via "
            "kai.enhancements.enable (requires HA_BASE_URL + HA_TOKEN in .env)")


@tool(ToolSpec(
    id="kai.home.devices", name="List HA devices",
    description="Home Assistant: list lights/switches/sensors/climate entities.",
    risk=SAFE, timeout_s=30.0, tags=["home"]))
def home_devices() -> dict:
    _home_gate()
    import requests, os
    url = os.environ["HA_BASE_URL"].rstrip("/")
    r = requests.get(f"{url}/api/states", headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
                     timeout=10)
    domains = {}
    for e in r.json():
        d = e["entity_id"].split(".")[0]
        if d in ("light", "switch", "sensor", "climate", "binary_sensor", "camera"):
            domains.setdefault(d, []).append(e["entity_id"])
    return {"domains": {k: len(v) for k, v in sorted(domains.items())},
            "entities": {k: v[:20] for k, v in sorted(domains.items())}}


@tool(ToolSpec(
    id="kai.home.set_state", name="Control HA device",
    description="Turn a light/switch on/off, set climate temperature. CONTROLLED.",
    risk=CONTROLLED, timeout_s=30.0, tags=["home"],
    inputs={"entity_id": "str", "action": "turn_on|turn_off|set_temp", "value": "any"}))
def home_set_state(entity_id: str, action: str, value=None) -> dict:
    _home_gate()
    import requests, os
    url = os.environ["HA_BASE_URL"].rstrip("/")
    domain = entity_id.split(".")[0]
    service = {"turn_on": "turn_on", "turn_off": "turn_off"}.get(action)
    payload = {"entity_id": entity_id}
    if action == "set_temp" and domain == "climate":
        service = "set_temperature"
        payload["temperature"] = float(value)
    elif not service:
        raise ValueError(f"unsupported action '{action}'")
    r = requests.post(f"{url}/api/services/{domain}/{service}",
                      headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
                      json=payload, timeout=10)
    return {"entity_id": entity_id, "action": action, "ok": r.status_code in (200, 201)}


@tool(ToolSpec(
    id="kai.voice.stream_transcribe", name="Streaming transcription",
    description="Realtime-style partial transcription of PCM16 16k audio. Requires 'streaming_voice' enhancement ENABLED.",
    risk=SAFE, timeout_s=150.0, tags=["voice"],
    inputs={"audio_b64": "str (base64 PCM16 16k mono)"}))
def voice_stream(audio_b64: str) -> dict:
    import base64 as b64
    from core.kai_enhancements import capability_available
    if not capability_available("kai.voice.streaming"):
        raise RuntimeError(
            "streaming_voice enhancement is DISABLED — enable via "
            "kai.enhancements.enable('streaming_voice')")
    from core.voice_router import transcribe_stream
    return transcribe_stream(b64.b64decode(audio_b64))


# --- kai.wireguard.* : peer creation via direct DD-WRT telnet (A-side) --------

def _ddwrt_telnet(command: str) -> str:
    """Run a command on DD-WRT via telnet. Runs from THIS host (A-side) —
    CT104/B-side cannot reach 192.168.99.66 (routing asymmetry)."""
    import os, telnetlib
    host = os.environ.get("DDWRT_HOST", "192.168.99.66")
    port = int(os.environ.get("DDWRT_TELNET_PORT", "23"))
    user = os.environ.get("DDWRT_USER", "root")
    pw = os.environ.get("DDWRT_PASSWORD", "")
    if not pw:
        raise RuntimeError("DDWRT_PASSWORD missing from orchestrator .env")
    tn = telnetlib.Telnet(host, port, timeout=15)
    tn.read_until(b"login:", timeout=10)
    tn.write(user.encode() + b"\n")
    tn.read_until(b"Password:", timeout=10)
    tn.write(pw.encode() + b"\n")
    tn.expect([rb"#\s*$", rb"\$\s*$"], timeout=10)
    tn.write(command.encode() + b"\n")
    out = tn.expect([rb"#\s*$", rb"\$\s*$"], timeout=15)[2].decode(errors="replace")
    tn.write(b"exit\n")
    tn.close()
    # strip the echoed command from the output
    return out.replace(command, "").strip()


@tool(ToolSpec(
    id="kai.wireguard.create_peer", name="Create WireGuard peer",
    description="Generate a new WG peer on the home DD-WRT router and return its config (QR-ready). HIGH RISK: grants network access.",
    risk=HIGH_RISK, timeout_s=300.0, tags=["network", "wireguard"],
    inputs={"server": "ddwrt", "label": "str"}))
def wireguard_create_peer(server: str, label: str) -> dict:
    import os, subprocess, re
    if server != "ddwrt":
        raise ValueError("only ddwrt supported")
    if not re.match(r"^[A-Za-z0-9 ._\-]{1,64}$", label):
        raise ValueError("invalid label")
    iface = os.environ.get("DDWRT_WG_INTERFACE", "wg0")
    subnet = os.environ.get("DDWRT_WG_SUBNET", "10.8.0.0/24")
    endpoint = os.environ.get("DDWRT_WG_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("DDWRT_WG_ENDPOINT missing from .env")

    # 1. generate client keypair (local, private stays local until config)
    kp = subprocess.run(["wg", "genkey"], capture_output=True, text=True)
    if kp.returncode != 0:
        raise RuntimeError("wg tool not installed on orchestrator host")
    priv = kp.stdout.strip()
    pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True).stdout.strip()

    # 2. next free IP: query existing peers from DD-WRT
    show = _ddwrt_telnet(f"wg show {iface} allowed-ips")
    used = set(re.findall(r"(\d+\.\d+\.\d+\.\d+)/", show))
    base_net, prefix = subnet.split("/")
    octets = base_net.split(".")
    free_ip = None
    for i in range(2, 255):
        cand = f"{octets[0]}.{octets[1]}.{octets[2]}.{i}"
        if cand not in used:
            free_ip = cand
            break
    if not free_ip:
        raise RuntimeError("subnet exhausted")

    # 3. add peer on DD-WRT (live) + persist in nvram rc_startup
    _ddwrt_telnet(f"wg set {iface} peer {pub} allowed-ips {free_ip}/32")
    _ddwrt_telnet(
        f"nvram set rc_startup=\"$(nvram get rc_startup)\nwg set {iface} peer {pub} allowed-ips {free_ip}/32\""
    )
    _ddwrt_telnet("nvram commit")

    # 4. server public key for the client config
    srv_pub = _ddwrt_telnet(f"wg show {iface} public-key")

    config_text = (
        f"[Interface]\n"
        f"PrivateKey = {priv}\n"
        f"Address = {free_ip}/32\n"
        f"DNS = 192.168.99.254\n\n"
        f"[Peer]\n"
        f"PublicKey = {srv_pub}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = 0.0.0.0/0\n"
        f"PersistentKeepalive = 25"
    )
    return {"created": True, "label": label, "address": free_ip,
            "config_text": config_text,
            "note": "config contains a private key — show as QR once, never store"}


# --- kai.enhancements.* : hardware-gated optional capabilities -------------------

@tool(ToolSpec(
    id="kai.enhancements.status", name="Enhancement status",
    description="Optional capabilities (wake word, streaming voice, Home Assistant, telephony): enabled × requirements = state.",
    risk=SAFE, tags=["enhancements"]))
def enhancements_status() -> dict:
    from core.kai_enhancements import status
    return {"enhancements": status()}


@tool(ToolSpec(
    id="kai.enhancements.enable", name="Enable enhancement",
    description="Opt into an enhancement. Can enable BEFORE hardware exists — auto-activates when requirements are met.",
    risk=CONTROLLED, tags=["enhancements"],
    inputs={"key": "wake_word|streaming_voice|home_assistant|telephony"}))
def enhancements_enable(key: str) -> dict:
    from core.kai_enhancements import enable
    return enable(key)


@tool(ToolSpec(
    id="kai.enhancements.disable", name="Disable enhancement",
    description="Turn an optional capability off.",
    risk=CONTROLLED, tags=["enhancements"],
    inputs={"key": "str"}))
def enhancements_disable(key: str) -> dict:
    from core.kai_enhancements import disable
    return disable(key)


# --- kai.home.* : Home Assistant control (§46) — gated by enhancement ----------

def _home_gate() -> None:
    from core.kai_enhancements import capability_available
    if not capability_available("kai.home.control"):
        raise RuntimeError(
            "Home Assistant integration is DISABLED/BLOCKED — enable it via "
            "kai.enhancements.enable (requires HA_BASE_URL + HA_TOKEN in .env)")


@tool(ToolSpec(
    id="kai.home.devices", name="List HA devices",
    description="Home Assistant: list lights/switches/sensors/climate entities.",
    risk=SAFE, timeout_s=30.0, tags=["home"]))
def home_devices() -> dict:
    _home_gate()
    import requests, os
    url = os.environ["HA_BASE_URL"].rstrip("/")
    r = requests.get(f"{url}/api/states", headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
                     timeout=10)
    domains = {}
    for e in r.json():
        d = e["entity_id"].split(".")[0]
        if d in ("light", "switch", "sensor", "climate", "binary_sensor", "camera"):
            domains.setdefault(d, []).append(e["entity_id"])
    return {"domains": {k: len(v) for k, v in sorted(domains.items())},
            "entities": {k: v[:20] for k, v in sorted(domains.items())}}


@tool(ToolSpec(
    id="kai.home.set_state", name="Control HA device",
    description="Turn a light/switch on/off, set climate temperature. CONTROLLED.",
    risk=CONTROLLED, timeout_s=30.0, tags=["home"],
    inputs={"entity_id": "str", "action": "turn_on|turn_off|set_temp", "value": "any"}))
def home_set_state(entity_id: str, action: str, value=None) -> dict:
    _home_gate()
    import requests, os
    url = os.environ["HA_BASE_URL"].rstrip("/")
    domain = entity_id.split(".")[0]
    service = {"turn_on": "turn_on", "turn_off": "turn_off"}.get(action)
    payload = {"entity_id": entity_id}
    if action == "set_temp" and domain == "climate":
        service = "set_temperature"
        payload["temperature"] = float(value)
    elif not service:
        raise ValueError(f"unsupported action '{action}'")
    r = requests.post(f"{url}/api/services/{domain}/{service}",
                      headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
                      json=payload, timeout=10)
    return {"entity_id": entity_id, "action": action, "ok": r.status_code in (200, 201)}


@tool(ToolSpec(
    id="kai.voice.stream_transcribe", name="Streaming transcription",
    description="Realtime-style partial transcription of PCM16 16k audio. Requires 'streaming_voice' enhancement ENABLED.",
    risk=SAFE, timeout_s=150.0, tags=["voice"],
    inputs={"audio_b64": "str (base64 PCM16 16k mono)"}))
def voice_stream(audio_b64: str) -> dict:
    import base64 as b64
    from core.kai_enhancements import capability_available
    if not capability_available("kai.voice.streaming"):
        raise RuntimeError(
            "streaming_voice enhancement is DISABLED — enable via "
            "kai.enhancements.enable('streaming_voice')")
    from core.voice_router import transcribe_stream
    return transcribe_stream(b64.b64decode(audio_b64))




# --- kai.factory.* : Android App Factory observability ---------------------------

FACTORY_HOST = "192.168.1.119"

def _factory_ssh(cmd: str, timeout: int = 25) -> str:
    import subprocess
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
                        f"root@{FACTORY_HOST}", cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"factory ssh failed: {r.stderr.strip()[:200]}")
    return r.stdout


@tool(ToolSpec(
    id="kai.factory.status", name="Factory status",
    description="Android App Factory health: disk, emulator state, projects, latest artifacts.",
    risk=SAFE, timeout_s=40.0, tags=["factory"]))
def factory_status() -> dict:
    disk = _factory_ssh("df -h / | tail -1 | awk '{print $5}'").strip()
    mem = _factory_ssh("free -h | awk '/Mem:/{print $3\"/\"$2}'").strip()
    emu = "running" if _factory_ssh("ps aux | grep -c [e]mulator").strip() != "0" else "stopped"
    projects = _factory_ssh("ls /opt/factory/projects | tr '\n' ' '").strip()
    latest = _factory_ssh("ls -t /opt/factory/artifacts/kai-ultimate 2>/dev/null | head -1").strip()
    return {"disk_used": disk, "memory": mem, "emulator": emu,
            "projects": projects, "latest_artifact": latest}


@tool(ToolSpec(
    id="kai.factory.build", name="Factory build",
    description="Run the full factory pipeline (build/test/scan/emulator/AAB) on a project.",
    risk=CONTROLLED, timeout_s=900.0, tags=["factory"],
    inputs={"project": "str"}))
def factory_build(project: str) -> dict:
    out = _factory_ssh(f"/opt/factory/pipeline-v2.sh /opt/factory/projects/{project} 2>&1", timeout=840)
    ok = "PIPELINE-PASS" in out
    art = [l for l in out.splitlines() if l.startswith(("PIPELINE-PASS:", "PIPELINE-FAIL:"))]
    return {"ok": ok, "report": out[-1500:], "artifacts": art[0].split(":",1)[1] if art else ""}


@tool(ToolSpec(
    id="kai.factory.reports", name="Factory reports",
    description="Recent build reports from the factory.",
    risk=SAFE, timeout_s=30.0, tags=["factory"]))
def factory_reports(limit: int = 5) -> dict:
    out = _factory_ssh(f"ls -t /opt/factory/artifacts/*/*/report.md 2>/dev/null | head -{min(limit,10)}")
    reports = []
    for p in out.strip().splitlines():
        content = _factory_ssh(f"cat {p} 2>/dev/null | head -20")
        reports.append({"path": p, "content": content})
    return {"count": len(reports), "reports": reports}
