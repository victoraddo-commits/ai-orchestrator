"""KAI Proactive Engine — JARVIS P13 (§23/§24).

OBSERVE → DETECT → CLASSIFY → CORRELATE → PREDICT → DECIDE → NOTIFY.

Observations come from existing collectors only:
  - health_observatory trends (linear regression over real samples)
  - world model entity statuses
  - pending approvals / cost signals via kai_executive

Predictions are explicitly labelled PREDICTION with confidence, never stated
as fact (§24/§49/§63). Findings that survive the noise filter go to
kai-notify severity routing (warn/critical reach Telegram; info stored).
Deterministic rules first — no LLM in the loop for detection.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"
PREDICTIONS_PATH = _MEMORY_DIR / "kai_predictions.json"

# Prediction thresholds
DISK_WARN_PCT = 75.0
DISK_CRITICAL_DAYS = 7.0      # "will hit X% within N days" horizon
MEM_WARN_PCT = 85.0
CPU_SUSTAINED_PCT = 90.0
MIN_R_SQUARED = 0.5           # below this a trend is noise, not a trend
MIN_SAMPLES = 12              # ~30min of samples at 30s... actually 5min cadence → 1h


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def observe() -> dict:
    """Gather current observations from verified sources."""
    out = {"ts": _now_iso(), "trends": {}, "world": {}, "approvals": 0}
    try:
        from core.health_observatory import get_trend, DB_PATH
        if Path(str(DB_PATH)).exists():
            for metric in ("host_disk_pct", "host_memory_pct", "host_cpu_pct"):
                out["trends"][metric] = get_trend(metric, window_hours=12)
    except Exception:
        pass
    try:
        from core.kai_executive import prioritize
        p = prioritize()
        out["world"] = {"critical": p["counts"]["critical"],
                        "attention": p["counts"]["attention"]}
    except Exception:
        pass
    return out


def predict(observations: dict | None = None) -> list:
    """Trend-based predictions with confidence. Each prediction states:
    what will happen, when, confidence, and the evidence."""
    if observations is None:
        observations = observe()
    predictions = []

    for metric, t in (observations.get("trends") or {}).items():
        cur = float(t.get("current_value") or 0)
        slope = float(t.get("slope_per_hour") or 0)   # percent-points/hour
        r2 = float(t.get("r_squared") or 0)
        samples = int(t.get("sample_count") or 0)

        if samples < MIN_SAMPLES or r2 < MIN_R_SQUARED or slope <= 0:
            continue

        conf = round(min(r2, 0.95), 2)

        if metric == "host_disk_pct":
            if cur >= DISK_WARN_PCT:
                hours_to_full = (100.0 - cur) / slope
                days = round(hours_to_full / 24, 1)
                predictions.append({
                    "kind": "prediction", "metric": metric,
                    "statement": f"Disk usage ({cur}%) trending up {slope:.2f}%/h — "
                                 f"projected full in ~{days} day(s)",
                    "confidence": conf, "horizon_days": min(days, 30),
                    "severity": "critical" if days <= DISK_CRITICAL_DAYS else "warn",
                    "evidence": f"{samples} samples, r²={r2:.2f}",
                })
        elif metric == "host_memory_pct" and cur >= MEM_WARN_PCT:
            predictions.append({
                "kind": "prediction", "metric": metric,
                "statement": f"Memory sustained at {cur}% and rising "
                             f"({slope:.2f}%/h) — OOM risk within hours",
                "confidence": conf, "severity": "critical",
                "evidence": f"{samples} samples, r²={r2:.2f}",
            })
        elif metric == "host_cpu_pct" and cur >= CPU_SUSTAINED_PCT and slope > 1.0:
            predictions.append({
                "kind": "prediction", "metric": metric,
                "statement": f"CPU at {cur}% with rising trend — saturation likely",
                "confidence": conf, "severity": "warn",
                "evidence": f"{samples} samples, r²={r2:.2f}",
            })

    # world-model criticals become immediate detections (fact, not prediction)
    w = observations.get("world") or {}
    if w.get("critical"):
        predictions.append({
            "kind": "detection",
            "statement": f"{w['critical']} infrastructure component(s) down/unreachable now",
            "confidence": 1.0, "severity": "critical", "evidence": "world model live status",
        })

    return predictions


def run_cycle(notify_threshold: str = "warn") -> dict:
    """One proactive loop pass: observe → predict → persist → notify.
    Only findings at/above notify_threshold reach Telegram (via kai-notify
    severity routing); everything is persisted regardless."""
    obs = observe()
    preds = predict(obs)

    sev_rank = {"info": 0, "warn": 1, "critical": 2}
    actionable = [p for p in preds
                  if sev_rank.get(p.get("severity"), 0) >= sev_rank[notify_threshold]]

    # persist history
    records = []
    try:
        import json
        rows = []
        try:
            with open(PREDICTIONS_PATH) as fh:
                rows = json.load(fh).get("records", [])
        except Exception:
            pass
        for p in preds:
            p.setdefault("ts", _now_iso())
        rows.extend(preds)
        rows = rows[-300:]
        tmp = PREDICTIONS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": 1, "records": rows}, default=str))
        os.replace(tmp, PREDICTIONS_PATH)
        records = preds
    except Exception:
        pass

    # deliver actionable ones through kai-notify (dedupes + rate limits itself)
    delivered = 0
    if actionable:
        try:
            from core.notifications import enqueue_from_findings
            delivered = enqueue_from_findings([
                {"title": p["statement"], "severity": p["severity"],
                 "source": "proactive-engine", "detail": p.get("evidence", "")}
                for p in actionable])
        except Exception:
            try:
                from core.telegram_bridge import send_message
                send_message("🔮 KAI Proactive:\n" + "\n".join(
                    f"• {p['statement']} (confidence {p['confidence']})" for p in actionable[:5]))
                delivered = len(actionable[:5])
            except Exception:
                pass

    return {"observed_at": obs["ts"], "predictions": preds,
            "actionable": len(actionable), "notified": delivered}


if __name__ == "__main__":
    print(run_cycle())
