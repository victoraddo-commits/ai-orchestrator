"""Source-health → Kai notifications (JURIS directive §64-§66).

Polls the legal brain's `/source-health` and raises KAI notifications for any
down approved source, so a failing source never fails silently. Uses the
existing notification system — this module does not create a scheduler.
"""
from __future__ import annotations

from core import legal_brain_client as lb


def check_and_notify(notifier=None) -> dict:
    try:
        health = lb.source_health()
    except Exception as exc:  # legal brain unreachable is itself notable
        if notifier is not None:
            notifier("legal_brain_unreachable", f"legal brain unreachable: {exc}")
        return {"error": str(exc), "down": [], "notified": 0}

    down = health.get("down", [])
    notified = 0
    for url in down:
        if notifier is not None:
            notifier("legal_source_down", url)
            notified += 1
    if notifier is None and down:
        try:
            from core.notifications import NotificationManager
            mgr = NotificationManager.get_instance()
            for url in down:
                mgr.enqueue(severity="important",
                            title=f"Legal source down: {url}",
                            body=url, source="legal_brain")
                notified += 1
        except Exception:
            notified = 0
    return {"down": down, "notified": notified,
            "total": health.get("total"), "ok": health.get("ok")}
