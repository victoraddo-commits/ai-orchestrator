"""Kai Core health probe — cycle timing + stall counts.

Added 2026-09-11 for phase 22A. Reads recent journalctl output for the
`ai-orchestrator.service` unit to compute:

  - last_started_at            ISO timestamp of the latest "cycle started"
  - last_completed_at          ISO timestamp of the latest "cycle completed"
  - running_now                True if last_started_at is more recent than
                               last_completed_at (or no completion followed)
  - avg_duration_sec           mean seconds between started/completed pairs
                               in the last N cycles (default 10)
  - stalls_last_24h            count of "advance_builds timed out" +
                               "stuck in ... for N cycles" WARNING lines
                               in the last 24h

Never raises — always returns a dict, populating what it can.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any


_JOURNAL_UNIT = "ai-orchestrator.service"
_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\b")


def _read_journal(since: str, unit: str = _JOURNAL_UNIT) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout or ""
    except Exception:
        return ""


def _parse_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    ts = m.group("ts").split(".")[0]
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _cycle_events(text: str) -> list[tuple[datetime, str]]:
    """Return [(ts, 'start'|'complete'), …] parsed from journal text."""
    events: list[tuple[datetime, str]] = []
    for line in text.splitlines():
        ts = _parse_ts(line)
        if not ts:
            continue
        if "orchestrator cycle started" in line:
            events.append((ts, "start"))
        elif "orchestrator cycle completed" in line:
            events.append((ts, "complete"))
    return events


def _pair_durations(events: list[tuple[datetime, str]], limit: int = 10) -> list[float]:
    """Match each 'start' with the next 'complete' after it. Return the last
    `limit` durations in seconds."""
    durs: list[float] = []
    i = 0
    n = len(events)
    while i < n:
        ts_i, kind_i = events[i]
        if kind_i != "start":
            i += 1
            continue
        # find next 'complete' after this start
        j = i + 1
        while j < n and events[j][1] != "complete":
            j += 1
        if j < n:
            durs.append((events[j][0] - ts_i).total_seconds())
            i = j + 1
        else:
            break
    return durs[-limit:]


def _count_stalls(text: str) -> int:
    n = 0
    for line in text.splitlines():
        if "advance_builds timed out" in line:
            n += 1
        elif "WARNING" in line and "stuck in" in line and "for " in line and "cycles" in line:
            n += 1
    return n


def get_health() -> dict[str, Any]:
    """Aggregate cycle-health snapshot. Never raises."""
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle": {
            "last_started_at": None,
            "last_completed_at": None,
            "running_now": None,
            "avg_duration_sec": None,
            "cycles_seen_last_hour": 0,
        },
        "stalls_last_24h": 0,
        "advance_builds_timeout_env": None,
        "stale_warn_cycles_env": None,
    }
    try:
        import os as _os
        result["advance_builds_timeout_env"] = int(_os.environ.get("KAI_ADVANCE_BUILDS_TIMEOUT", "300"))
        result["stale_warn_cycles_env"] = int(_os.environ.get("KAI_STALE_WARN_CYCLES", "2"))
    except Exception:
        pass

    text_1h = _read_journal("1 hour ago")
    events = _cycle_events(text_1h)
    if events:
        result["cycle"]["cycles_seen_last_hour"] = sum(1 for _, k in events if k == "start")
        starts = [ts for ts, k in events if k == "start"]
        completes = [ts for ts, k in events if k == "complete"]
        if starts:
            result["cycle"]["last_started_at"] = starts[-1].isoformat()
        if completes:
            result["cycle"]["last_completed_at"] = completes[-1].isoformat()
        if starts and completes:
            result["cycle"]["running_now"] = starts[-1] > completes[-1]
        elif starts:
            result["cycle"]["running_now"] = True
        durs = _pair_durations(events, limit=10)
        if durs:
            result["cycle"]["avg_duration_sec"] = round(sum(durs) / len(durs), 2)

    text_24h = _read_journal("24 hours ago")
    result["stalls_last_24h"] = _count_stalls(text_24h)

    return result
