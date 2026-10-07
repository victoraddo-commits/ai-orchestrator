"""Observability snapshot (roadmap 24A).

Builds one structured view — health, metrics, traces, timeline — from the
lifecycle objects (incidents/decisions/approvals/remediations/verifications)
so the Command Center /diagnostics page and /api/observability read a single
shape. Injection-friendly; no live imports at module load.
"""
from __future__ import annotations

KINDS = ("incident", "decision", "approval", "remediation", "verification")


def build_timeline(objects) -> list:
    events = []
    for kind, records in (objects or {}).items():
        for rec in (records or []):
            entry = dict(rec)
            entry["kind"] = kind
            events.append(entry)
    events.sort(key=lambda e: str(e.get("timestamp") or e.get("created_at") or ""))
    return events


def snapshot(objects=None, health=None, metrics=None, traces=None) -> dict:
    objects = objects or {}
    timeline = build_timeline(objects)
    counts = {kind: len(objects.get(kind, []) or []) for kind in KINDS}
    anomalies = [e for e in timeline
                 if str(e.get("status") or e.get("state") or "").lower()
                 in ("failed", "error", "degraded", "rolled_back")]
    return {
        "schema": "observability/1",
        "health": health or {},
        "metrics": metrics or {},
        "traces": traces or [],
        "timeline": timeline,
        "counts": counts,
        "anomalies": anomalies,
    }
