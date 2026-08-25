"""KAI Self-Evolution Engine — master spec 2026-08-25.

INTEGRATION LAYER over existing verified systems (§61 do-not-duplicate):
  - detection:     health.py analyze() + proactive engine (already running)
  - remediation:   remediation_runner + approval queue (human-gated)
  - autonomy:      core/autonomy.py levels 0-5 (existing)
  - proposals:     improvement_proposals.json + kai_executive memory
  - kill switch:   core/kai_emergency.py (existing)
  - audit:         tool_audit.jsonl + lifecycle objects

WHAT'S NEW HERE:
  - risk classification for every proposed change (§9)
  - evolution memory: incidents→fixes→regression tests (§32)
  - anti-loop protection (§34): oscillation + budget detection
  - snapshot records before any approved change (§13)
  - the unified EVOLUTION PIPELINE state machine (§2)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_DIR = Path("/project/ai-orchestrator/memory")
EVOLUTION_PATH = _MEMORY_DIR / "kai_evolution.json"
SNAPSHOTS_DIR = _MEMORY_DIR / "evolution_snapshots"

# §9 risk classification
RISK_RULES = [
    ("critical", ["approval", "vault", "audit", "emergency", "autonomy", "safeguard", "permission", "identity"]),
    ("high", ["money", "treasury", "tangem", "financial", "network", "wireguard", "security", "auth"]),
    ("medium", ["api", "worker", "dependency", "config", "deploy", "provider"]),
    ("low", ["docs", "log", "ui", "comment", "readme", "cache"]),
]

# §34 anti-loop
MAX_CHANGES_PER_DAY = 10
MAX_SAME_TARGET_PER_DAY = 3


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        with open(EVOLUTION_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"schema_version": 1, "changes": [], "incidents": [], "snapshots": []}


def _save(d: dict) -> None:
    tmp = EVOLUTION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, default=str))
    os.replace(tmp, EVOLUTION_PATH)


def classify_risk(title: str, target: str = "") -> str:
    text = (title + " " + target).lower()
    for level, keywords in RISK_RULES:
        if any(k in text for k in keywords):
            return level
    return "low"


def anti_loop_ok(target: str) -> tuple[bool, str]:
    """§34: budget + oscillation checks. Returns (ok, reason)."""
    d = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays = [c for c in d["changes"] if str(c.get("ts", "")).startswith(today)
              and c.get("decision") in ("deployed", "auto_deployed")]
    if len(todays) >= MAX_CHANGES_PER_DAY:
        return False, f"daily change budget reached ({MAX_CHANGES_PER_DAY})"
    same = [c for c in todays if c.get("target") == target]
    if len(same) >= MAX_SAME_TARGET_PER_DAY:
        return False, f"target '{target}' changed {len(same)}x today — oscillation suspected, escalating to human"
    # oscillation: same target changed with alternating outcomes
    if len(same) >= 2:
        outcomes = [c.get("result") for c in same[-2:]]
        if outcomes[0] != outcomes[1] and "rollback" in str(outcomes).lower():
            return False, "rollback oscillation detected — human review required"
    return True, ""


def snapshot(label: str) -> dict:
    """§13: capture current state before a change."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap = {
        "label": label, "ts": _now(),
        "autonomy_level": None, "git": {},
    }
    try:
        import subprocess
        for repo, name in [("/project/ai-orchestrator", "orchestrator")]:
            r = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                               capture_output=True, text=True, timeout=10)
            snap["git"][name] = r.stdout.strip()
        from core.autonomy import get_level
        snap["autonomy_level"] = get_level()
    except Exception:
        pass
    d = _load()
    d["snapshots"].append(snap)
    d["snapshots"] = d["snapshots"][-50:]
    _save(d)
    return snap


def propose_change(title: str, target: str, reason: str, change_summary: str,
                   source: str = "self-evolution") -> dict:
    """§2 pipeline: PROPOSE stage. Risk-classified; anti-loop checked.
    LOW risk + high autonomy → may auto-approve. Else → human approval."""
    from core.kai_tools.builtin import selfimprove_propose
    risk = classify_risk(title, target)
    loop_ok, loop_reason = anti_loop_ok(target)
    if not loop_ok:
        # §34: escalate, never auto-proceed
        prop = selfimprove_propose(f"[BLOCKED-LOOP] {title}",
                                   f"Anti-loop: {loop_reason}", change_summary)
        return {"ok": False, "blocked": True, "reason": loop_reason, "proposal_id": prop["id"]}
    snap = snapshot(f"before: {title}")
    autonomy = None
    try:
        from core.kai_tools.policy import current_autonomy_level
        autonomy = current_autonomy_level()
    except Exception:
        pass
    # §40 approval matrix
    if risk == "low" and (autonomy or 0) >= 4:
        decision = "auto_approved"
    elif risk == "medium" and (autonomy or 0) >= 4:
        decision = "auto_approved_pending_review"
    else:
        decision = "needs_human"
    d = _load()
    change = {
        "id": f"evo-{int(time.time()*1000)%100000}", "ts": _now(),
        "title": title, "target": target, "risk": risk,
        "reason": reason, "change_summary": change_summary,
        "decision": decision, "result": None,
        "snapshot": snap.get("git", {}), "source": source,
    }
    d["changes"].append(change)
    d["changes"] = d["changes"][-200:]
    _save(d)
    # also surface in the existing improvement proposals for CC visibility
    try:
        selfimprove_propose(title, reason, change_summary)
    except Exception:
        pass
    return {"ok": True, "id": change["id"], "risk": risk, "decision": decision,
            "snapshot": snap.get("git", {})}


def record_result(change_id: str, result: str, detail: str = "") -> None:
    """§2 final stage: record deployed/rolled_back/failed + learn."""
    d = _load()
    for c in d["changes"]:
        if c["id"] == change_id:
            c["result"] = result
            c["result_detail"] = detail
            c["completed_at"] = _now()
            # §32 evolution memory: failures become lessons
            if result in ("failed", "rolled_back"):
                try:
                    from core.kai_executive import remember_failure
                    remember_failure(c["title"], cause=detail or result,
                                     lesson=f"change to {c['target']} at risk {c['risk']} — review before retrying",
                                     verified=False, source="self-evolution")
                except Exception:
                    pass
            break
    _save(d)


def status() -> dict:
    d = _load()
    recent = d["changes"][-20:][::-1]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "changes_today": len([c for c in d["changes"] if str(c.get("ts","")).startswith(today)]),
        "daily_budget": MAX_CHANGES_PER_DAY,
        "recent": [{"id": c["id"], "title": c["title"], "risk": c["risk"],
                    "decision": c["decision"], "result": c.get("result")} for c in recent],
        "snapshots": len(d["snapshots"]),
    }
