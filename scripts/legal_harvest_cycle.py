#!/usr/bin/env python3
"""Weekly legal harvest job — CT 111 scheduler side (Legal Brain 2.0, Task 8).

Triggers exactly one bounded harvest cycle on the legal brain (CT 100 :8100)
via the read/write client and sends a Telegram summary. The heavy lifting
(discovery, full-text resolution, rights gating, ingest, dedup, stub
re-harvest) happens on the brain in ``core/legal/harvest_cycle.py``; this script
only schedules it, guards against overlap and reports.

    python3 scripts/legal_harvest_cycle.py --limit 5 --stub-limit 3 --delay 1

Single-instance: an exclusive ``flock`` on ``LEGAL_HARVEST_LOCK`` means a second
trigger (or a manual run while the timer fires) exits immediately instead of
hammering the same sources. Installed as ``kai-legal-harvest.timer`` (weekly).
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import legal_brain_client as lb  # noqa: E402

LOCK_PATH = os.environ.get("LEGAL_HARVEST_LOCK",
                           "/var/lib/ai-orchestrator/legal_harvest.lock")

DEFAULT_LIMIT = 5
DEFAULT_STUB_LIMIT = 3
DEFAULT_DELAY = 1.0


def acquire_lock(path: str):
    """Take a non-blocking exclusive lock; return the fd, or None if held."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_lock(fd) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def format_summary(report: dict) -> str:
    """Build the Telegram summary for a cycle report (or a skip/error)."""
    header = "⚖️ *Legal Brain 2.0 — weekly harvest*"
    if not isinstance(report, dict):
        return f"{header}\n\n⚠️ Unexpected response: {report!r}"
    if report.get("skipped"):
        return f"{header}\n\n⏭️ Skipped: {report['skipped']}"
    if report.get("ok") is False or report.get("error"):
        reason = report.get("error") or report.get("skipped") or "unknown error"
        return f"{header}\n\n❌ Run failed: {reason}"

    totals = report.get("totals") or {}
    cov = report.get("coverage") or {}
    delta = cov.get("delta") or {}
    stubs = report.get("stubs") or {}
    stub_summary = stubs.get("summary") or {}
    before = (cov.get("before") or {}).get("with_content", "?")
    after = (cov.get("after") or {}).get("with_content", "?")
    weak_after = delta.get("weak_after") or []
    names = report.get("sources_used") or [
        s.get("id") for s in report.get("sources", [])]

    lines = [
        header,
        f"Sources: {', '.join(n for n in names if n) or '(none)'}",
        (f"Docs: new={totals.get('new', 0)} updated={totals.get('updated', 0)} "
         f"dup={totals.get('duplicates', 0)} skipped={totals.get('skipped', 0)} "
         f"failed={totals.get('failed', 0)}"),
        (f"Coverage: {before} → {after} "
         f"({delta.get('with_content', 0):+d} with real content)"),
        (f"Stubs: selected={stubs.get('selected', 0)} "
         f"upgraded={stub_summary.get('updated', 0)} "
         f"failed={stub_summary.get('failed', 0)}"),
    ]
    if weak_after:
        lines.append(f"Weak areas: {', '.join(weak_after)}")
    failures = (totals.get("failures") or []) + \
        (stub_summary.get("failures") or [])
    if failures:
        lines.append(f"Failures ({len(failures)}):")
        for f in failures[:5]:
            lines.append(f"  - {f.get('title', '')[:50]}: "
                         f"{f.get('reason', '')[:80]}")
    paths = report.get("report_paths") or []
    if paths:
        lines.append(f"Report: `{os.path.basename(paths[0])}`")
    # Legal change watcher (Phase 6 T1): the brain embeds the bounded
    # LEGAL CHANGE ALERT in its run report; relay it through the same Telegram
    # notify path. A Bill is always labelled PROPOSED — never law.
    legal = report.get("legal_changes")
    if isinstance(legal, dict) and legal.get("alert"):
        lines.append("")
        lines.append(legal["alert"])
    return "\n".join(lines)


def run(*, limit: int = DEFAULT_LIMIT, stub_limit: int = DEFAULT_STUB_LIMIT,
        delay: float = DEFAULT_DELAY, dry_run: bool = False, notify: bool = True,
        lock_path: str = None, client=None, alert=None) -> dict:
    """Run one scheduled harvest cycle (locked) and optionally notify.

    Returns ``{"skipped"|"summary"|"report", ...}``. Never raises for a brain
    error — the failure is reported in the summary instead.
    """
    lock_path = lock_path or LOCK_PATH
    fd = acquire_lock(lock_path)
    if fd is None:
        return {"skipped": "already running", "lock": lock_path}
    try:
        if client is None:
            client = lb
        if alert is None:
            from core.telegram_bridge import send_telegram_alert
            alert = send_telegram_alert
        try:
            report = client.harvest_cycle(limit=limit, stub_limit=stub_limit,
                                          delay=delay, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - a scheduler job must not crash
            report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        summary = format_summary(report)
        if notify:
            try:
                alert(summary)
            except Exception as exc:  # noqa: BLE001 - notify must not mask the run
                summary = summary + f"\n\n(telegram send failed: {exc})"
        return {"summary": summary, "report": report}
    finally:
        release_lock(fd)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--stub-limit", type=int, default=DEFAULT_STUB_LIMIT)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    result = run(limit=args.limit, stub_limit=args.stub_limit, delay=args.delay,
                 dry_run=args.dry_run, notify=not args.no_notify)
    if result.get("skipped"):
        print("skipped:", result["skipped"])
        return
    print(result["summary"])


if __name__ == "__main__":
    main()
