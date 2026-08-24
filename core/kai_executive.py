"""KAI Executive — JARVIS P5 + P10.

Two jobs:

1. Typed memory stores (§29/§30/§31) on the existing memory_manager pattern:
   decision memory (what we chose and WHY), failure memory (verified
   failures + lessons), operational memory (what KAI did). Structured
   records with source/ts/confidence so they're inspectable.

2. Executive prioritization + briefings (§13/§14): aggregate world model,
   costs, alerts, approvals, money state into "what matters right now",
   classified ACT / MONITOR / NOTIFY. Generates morning/evening/on-demand
   briefings for Telegram. Deterministic aggregation FIRST (facts), with an
   optional LLM pass for phrasing — never fabricating data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"

DECISIONS_PATH = _MEMORY_DIR / "kai_decisions.json"
FAILURES_PATH = _MEMORY_DIR / "kai_failures.json"
OPERATIONS_PATH = _MEMORY_DIR / "kai_operations_log.json"
BRIEFINGS_PATH = _MEMORY_DIR / "kai_briefings.json"

_SCHEMA = {"schema_version": 1}


def _load(path: Path) -> list:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d.get("records", [])
    except Exception:
        return []


def _append(path: Path, record: dict, cap: int = 500) -> dict:
    records = _load(path)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    records.append(record)
    records = records[-cap:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**_SCHEMA, "records": records}, default=str))
    os.replace(tmp, path)
    return record


# --- typed memory API --------------------------------------------------------

def remember_decision(decision: str, reason: str, alternatives: list | None = None,
                      status: str = "active", review_when: str | None = None,
                      source: str = "operator", confidence: float = 0.9) -> dict:
    """§30 structured decision record."""
    return _append(DECISIONS_PATH, {
        "kind": "decision", "decision": decision, "reason": reason,
        "alternatives": alternatives or [], "status": status,
        "review_when": review_when, "source": source, "confidence": confidence,
    })


def remember_failure(action: str, cause: str, remediation: str | None = None,
                     lesson: str | None = None, verified: bool = False,
                     source: str = "system") -> dict:
    """§31 failure memory. Speculative lessons are marked unverified and must
    never be presented as fact."""
    return _append(FAILURES_PATH, {
        "kind": "failure", "action": action, "cause": cause,
        "remediation": remediation, "lesson": lesson,
        "verified": verified,  # True only after a successful re-run post-fix
        "source": source,
    })


def log_operation(tool_id: str, summary: str, ok: bool, details: dict | None = None,
                  actor: str = "kai") -> dict:
    """Operational memory — what KAI actually did (evidence-backed)."""
    return _append(OPERATIONS_PATH, {
        "kind": "operation", "tool": tool_id, "summary": summary,
        "ok": ok, "details": details or {}, "actor": actor,
    }, cap=2000)


def recent_decisions(limit: int = 20) -> list:
    return _load(DECISIONS_PATH)[-limit:][::-1]


def recent_failures(limit: int = 20, verified_only: bool = False) -> list:
    rows = _load(FAILURES_PATH)[-limit:][::-1]
    if verified_only:
        rows = [r for r in rows if r.get("verified")]
    return rows


# --- executive aggregation ----------------------------------------------------

def _world_changes() -> list:
    try:
        with open(_MEMORY_DIR / "world_model.json") as fh:
            snap = json.load(fh)
        # changes are vs previous snapshot; keep last refresh's view
        out = []
        for eid, e in (snap.get("entities") or {}).items():
            st = str(e.get("status", "")).lower()
            if e.get("type") in ("proxmox_node", "lxc", "vm") and st not in ("online", "running"):
                out.append({"entity": eid, "label": e.get("label"), "status": st,
                            "severity": "critical" if st == "unreachable" else "warn"})
        return out[:15]
    except Exception:
        return []


def _pending_approvals() -> list:
    try:
        from core import approval
        rows = approval.list_pending()
        return [{"id": r.get("id"), "action": r.get("action"),
                 "reason": str(r.get("reason", ""))[:90]} for r in rows[:10]]
    except Exception:
        return []


def _cost_signal() -> dict:
    try:
        from core.ai.cost_tracker import get_cost_summary
        s = get_cost_summary(days=1)
        return {"today_cost": s.get("total_cost"),
                "today_calls": s.get("total_calls")}
    except Exception:
        return {}


def _disk_signals() -> list:
    out = []
    try:
        import shutil
        u = shutil.disk_usage("/")
        pct = round(u.used / u.total * 100, 1)
        if pct > 80:
            out.append({"severity": "warn" if pct < 90 else "critical",
                        "entity": "host:pve-a disk", "detail": f"{pct}% used"})
    except Exception:
        pass
    return out


def prioritize() -> dict:
    """Executive KAI: classify everything into what matters now (§13/§40).
    Returns needs_attention (ACT), watch (MONITOR), info counts."""
    critical, attention, watch = [], [], []

    for c in _world_changes():
        (critical if c.get("severity") == "critical" else watch).append(c)
    for d in _disk_signals():
        (attention if d.get("severity") != "critical" else critical).append(d)
    approvals = _pending_approvals()
    # §41 correlation: group same-action+similar-reason approvals into ONE
    # item ("N approvals share this root cause") instead of N lines.
    seen_roots: dict[str, dict] = {}
    for a in approvals:
        root = f"{a['action']}|{str(a.get('reason',''))[:60]}"
        if root in seen_roots:
            seen_roots[root]["count"] += 1
        else:
            seen_roots[root] = {"entity": f"approval:{a['id']}", "action": a["action"],
                                "detail": f"{a['action']}: {a['reason']}",
                                "severity": "approval", "count": 1}
    for grouped in seen_roots.values():
        if grouped.pop("count") > 1:
            grouped["detail"] += " (multiple instances — one root cause)"
        attention.append(grouped)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "critical": critical,
        "needs_attention": attention,
        "watch": watch,
        "counts": {
            "critical": len(critical),
            "attention": len(attention),
            "watch": len(watch),
            "approvals_pending": len(approvals),
        },
    }


def _fmt_briefing(p: dict, cost: dict) -> str:
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = "🌅 KAI Briefing" if datetime.now(timezone.utc).hour < 12 else "🌆 KAI Evening Briefing"
    lines.append(f"{title} — {ts}")

    c, a, w = p["critical"], p["needs_attention"], p["watch"]
    if not c and not a:
        lines.append("\n✅ Everything important is under control.")
    else:
        if c:
            lines.append(f"\n🚨 Critical ({len(c)}):")
            lines += [f"  • {x.get('label') or x.get('entity')}: {x.get('status') or x.get('detail','')}" for x in c[:5]]
        if a:
            lines.append(f"\n⚠️ Needs your attention ({len(a)}):")
            lines += [f"  • {x.get('detail') or x.get('entity')}" for x in a[:6]]
    if w:
        lines.append(f"\n👀 Watching ({len(w)}, non-urgent): {', '.join(str(x.get('label') or x.get('entity')) for x in w[:4])}")
    if cost.get("today_cost") is not None:
        lines.append(f"\n💰 AI spend today: ${float(cost.get('today_cost') or 0):.2f} ({cost.get('today_calls', 0)} calls)")
    lines.append("\nSay 'handle whatever you safely can' to let me act within policy.")
    return "\n".join(lines)


def run_briefing(kind: str = "auto", send: bool = True) -> str:
    """Generate (and optionally deliver) an executive briefing."""
    p = prioritize()
    cost = _cost_signal()
    text = _fmt_briefing(p, cost)
    if kind != "auto":
        text = text.replace("KAI Briefing", f"KAI {kind.title()} Briefing", 1)
    _append(BRIEFINGS_PATH, {"kind": kind, "text": text,
                             "counts": p["counts"]}, cap=200)
    if send:
        try:
            from core.telegram_bridge import send_message
            send_message(text)
        except Exception:
            pass
    return text
