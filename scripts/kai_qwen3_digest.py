"""Hourly Kai Telegram digest of Qwen3-Coder job activity.

2026-08-02 operator directive: periodic updates on how much work the
newly-wired Qwen3-Coder-30B-A3B-Instruct-AWQ routes are actually doing.
Standalone cron entry rather than a hook into core.scheduler's 300s
infra-remediation cycle -- unrelated concern, and this only needs to run
once an hour.

Two separate provider registrations both run on the same RunPod GPU, so
both are covered -- missing either one would hide half of Qwen3's real
activity from this "keep me updated" digest:
  - "openai": text_task Q&A/review route (core.llm_clients.call_openai)
  - "qwen4_coding": coding_agent route driving opencode's tool-use loop,
    the roadmap's primary builder as of 2026-08-02 (core.ai_provider's
    QWEN3_CODING_MODEL)

Usage: .venv/bin/python scripts/kai_qwen3_digest.py

2026-08-02: window/label are now parameters so scripts/kai_qwen3_daily_digest.py
can reuse this summarization logic with a 24h window instead of duplicating it.
Called with no args this still behaves exactly as the original hourly cron entry.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.ai.ai_router as ai_router  # noqa: E402
import core.telegram_bridge as telegram_bridge  # noqa: E402

DEFAULT_WINDOW = timedelta(hours=1)
DEFAULT_LABEL = "hourly"

PROVIDERS = ("openai", "qwen4_coding")
PROVIDER_LABEL = {"openai": "Q&A/review", "qwen4_coding": "roadmap builds"}


def _window_phrase(window):
    # 2026-08-02: human phrasing for the "no jobs" line -- "hour" reads better
    # than "1 hours" for the original hourly digest, everything else falls back
    # to a plain hour count (24 hours for the daily variant).
    hours = window.total_seconds() / 3600
    if hours == 1:
        return "hour"
    if hours == int(hours):
        return f"{int(hours)} hours"
    return f"{hours:g} hours"


def _recent_qwen4_records(now=None, window=DEFAULT_WINDOW):
    now = now or datetime.now()
    cutoff = now - window

    records = []
    for record in ai_router.get_usage_history():
        if record.get("provider") not in PROVIDERS:
            continue
        try:
            timestamp = datetime.fromisoformat(record["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            records.append(record)

    return records


def _summarize(records):
    total = len(records)
    succeeded = sum(1 for r in records if r.get("success"))
    failed = total - succeeded

    durations = [r["duration_ms"] for r in records if r.get("duration_ms") is not None]
    avg_ms = int(sum(durations) / len(durations)) if durations else None

    lines = [f"Jobs: {total} ({succeeded} ok, {failed} failed)"]
    if avg_ms is not None:
        lines.append(f"Avg duration: {avg_ms}ms")

    if failed:
        sample_error = next((r.get("error") for r in records if not r.get("success") and r.get("error")), None)
        if sample_error:
            lines.append(f"Sample error: {sample_error[:200]}")

    return lines


def build_digest_message(records, now=None, window=DEFAULT_WINDOW, label=DEFAULT_LABEL):
    now = now or datetime.now()
    timestamp_label = now.strftime("%Y-%m-%d %H:%M")

    if not records:
        return (
            f"\U0001f4ca Kai / Qwen3 {label} update\n"
            f"No Qwen3 jobs in the last {_window_phrase(window)} (as of {timestamp_label})."
        )

    lines = [f"\U0001f4ca Kai / Qwen3 {label} update ({timestamp_label})"]

    for provider in PROVIDERS:
        provider_records = [r for r in records if r.get("provider") == provider]
        if not provider_records:
            continue
        lines.append(f"\n{provider} ({PROVIDER_LABEL[provider]}):")
        lines.extend(f"  {line}" for line in _summarize(provider_records))

    return "\n".join(lines)


def main(window=DEFAULT_WINDOW, label=DEFAULT_LABEL):
    records = _recent_qwen4_records(window=window)
    message = build_digest_message(records, window=window, label=label)
    telegram_bridge.send_message(message)
    print(message)


if __name__ == "__main__":
    main()
