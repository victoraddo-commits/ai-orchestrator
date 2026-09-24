#!/usr/bin/env python3
"""Ask-to-Acquire scheduler pass + user notification (Phase 7, Task 3).

Bounded and polite: lists pending gaps on the legal brain, runs one bounded
acquisition pass per gap, then notifies the **asker** and the **operator** once
per gap (the brain's ``notified_at`` is the one-shot guard, so a gap is never
announced twice).

    .venv/bin/python scripts/legal_gap_acquire.py --limit 5 --per-source 5
    .venv/bin/python scripts/legal_gap_acquire.py --dry-run

Driven by ``kai-legal-gap-acquire.timer`` (every 6h). The brain does the
searching/ingesting (enactments only, licence-gated); this script only triggers
it and tells the humans.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import legal_brain_client as lb  # noqa: E402
from core.telegram_bridge import send_telegram_alert  # noqa: E402

FILLED_ASKER = (
    "⚖️ Good news — I've now added *{inst}* to my legal database. "
    "Ask your question again and I'll answer it with the source.")
NOT_FOUND_ASKER = (
    "⚖️ I couldn't find an authoritative Ghanaian source for your question yet. "
    "I've escalated it to our legal team and will add it if we can obtain it "
    "lawfully.")
FILLED_OPERATOR = (
    "Ask-to-Acquire: gap #{id} *FILLED* — {inst}.\nQ: {q}")
NOT_FOUND_OPERATOR = (
    "Ask-to-Acquire: gap #{id} *not found*. Sources tried: {src}.\nQ: {q}")
REVIEW_OPERATOR = (
    "Ask-to-Acquire: gap #{id} *needs review* ({reason}).\nQ: {q}")


def _is_chat_id(value) -> bool:
    return bool(value) and str(value).lstrip("-").isdigit()


def _instrument_label(gap: dict) -> str:
    ids = gap.get("filled_doc_ids") or []
    if not ids:
        return "the missing instrument"
    try:
        doc = lb.get_document(int(ids[0])) or {}
        title = (doc.get("title") or "").strip()
        if title:
            return title
    except Exception:  # noqa: BLE001 - label is best-effort
        pass
    return f"document #{ids[0]}"


def notify_gap(gap: dict, *, send=send_telegram_alert, operator_chat=None) -> dict:
    """Notify the asker + operator for one gap; mark it notified (one-shot)."""
    gap = gap or {}
    gid = gap.get("id")
    if gap.get("notified_at"):
        return {"gap_id": gid, "skipped": "already notified"}
    status = gap.get("status")
    asker = gap.get("asked_by") or ""
    question = (gap.get("question") or "")[:200]
    delivered = []
    if status == "filled":
        inst = _instrument_label(gap)
        if _is_chat_id(asker):
            delivered.append(bool(send(FILLED_ASKER.format(inst=inst),
                                       chat_id=asker)))
        send(FILLED_OPERATOR.format(id=gid, inst=inst, q=question),
             chat_id=operator_chat)
    elif status == "not_found":
        if _is_chat_id(asker):
            delivered.append(bool(send(NOT_FOUND_ASKER, chat_id=asker)))
        tried = ", ".join(gap.get("sources_tried") or []) or "(none)"
        send(NOT_FOUND_OPERATOR.format(id=gid, src=tried, q=question),
             chat_id=operator_chat)
    elif status == "needs_review":
        evidence = gap.get("evidence") or ""
        if isinstance(evidence, dict):
            evidence = evidence.get("reason") or json.dumps(evidence)[:160]
        send(REVIEW_OPERATOR.format(id=gid, reason=str(evidence)[:160],
                                    q=question), chat_id=operator_chat)
    else:
        return {"gap_id": gid, "skipped": f"status {status}"}
    try:
        lb.mark_gap_notified(gid)
    except Exception as exc:  # noqa: BLE001 - notification already sent
        return {"gap_id": gid, "status": status, "notified": True,
                "guard_error": f"{type(exc).__name__}: {exc}"}
    return {"gap_id": gid, "status": status, "notified": True,
            "asker_delivered": any(delivered)}


def run_pass(*, limit: int = 5, per_source: int = 5, delay: float = 0.5,
             notify: bool = True, send=send_telegram_alert,
             operator_chat=None, dry_run: bool = False) -> dict:
    """One bounded acquire+notify pass; returns an honest report."""
    if dry_run:
        pending = lb.list_gaps(status="pending", limit=limit)
        return {"dry_run": True,
                "pending": [{"id": g.get("id"), "question": g.get("question"),
                             "attempts": g.get("attempts")} for g in pending]}

    report: dict = {"acquired": [], "notified": [], "counts": {}}
    for gap in lb.list_gaps(status="pending", limit=limit):
        out = lb.acquire_gap(gap.get("id"), per_source=per_source, delay=delay)
        report["acquired"].append({
            "gap_id": gap.get("id"), "question": gap.get("question"),
            "status": out.get("status"), "error": out.get("error"),
            "doc_ids": out.get("doc_ids"),
            "sources_tried": out.get("sources_tried")})

    if notify:
        for status in ("filled", "not_found", "needs_review"):
            for gap in lb.list_gaps(status=status, limit=limit):
                if gap.get("notified_at"):
                    continue
                report["notified"].append(notify_gap(
                    gap, send=send, operator_chat=operator_chat))

    for a in report["acquired"]:
        key = a.get("status") or "error"
        report["counts"][key] = report["counts"].get(key, 0) + 1
    return report


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=5,
                    help="pending gaps per pass (bounded)")
    ap.add_argument("--per-source", type=int, default=5,
                    help="candidates per source on the brain (bounded)")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="seconds between sources (polite, min 0.5)")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    report = run_pass(limit=args.limit, per_source=args.per_source,
                      delay=args.delay, notify=not args.no_notify,
                      dry_run=args.dry_run)
    print(json.dumps(report, indent=2, default=str))
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)),
                    exist_ok=True)
        with open(args.json_out, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        print("json:", args.json_out)


if __name__ == "__main__":
    main()
