"""AI-5: Cost Tracking — per-provider cost aggregation and reporting.

Reads ai_usage_history and applies provider_pricing to compute actual
costs. Supports aggregation by provider, task_type (role), and time
period (daily/weekly/monthly). Used both by the API dashboard and the
budget monitor.

Costs in ai_usage_history are nullable — entries without recorded cost
are estimated from pricing data when available.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core.ai.provider_pricing import get_pricing, compute_cost as _compute_call_cost
from core.logger import info


def _load_history():
    """Load raw usage history from memory/ai_usage_history.json."""
    try:
        from core.ai.ai_router import get_usage_history

        return get_usage_history()
    except Exception:
        path = Path("memory/ai_usage_history.json")
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError):
            return []


def get_cost_summary(days: int = 30) -> dict:
    """Return cost summary for the last N days.

    Returns:
        {
            "total_cost": float,
            "by_provider": {provider: cost, ...},
            "by_role": {task_type: cost, ...},
            "daily": [{date: str, cost: float, calls: int}, ...],
            "period_days": int,
            "calls_with_cost": int,
            "calls_estimated": int,
            "calls_unknown": int,
        }
    """
    history = _load_history()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    by_provider = defaultdict(float)
    by_role = defaultdict(float)
    daily = defaultdict(lambda: {"cost": 0.0, "calls": 0})
    calls_with_cost = 0
    calls_estimated = 0
    calls_unknown = 0

    for entry in history:
        ts = entry.get("timestamp", "")
        if ts < cutoff:
            continue

        provider = entry.get("provider", "unknown")
        task_type = entry.get("task_type", "unknown")
        recorded_cost = entry.get("cost")

        if isinstance(recorded_cost, (int, float)) and not isinstance(
            recorded_cost, bool
        ):
            cost = float(recorded_cost)
            calls_with_cost += 1
        else:
            # Estimate from pricing
            prompt_tokens = entry.get("usage", {}).get(
                "prompt_tokens", (len(entry.get("description", "")) // 4)
            )
            completion_tokens = entry.get("usage", {}).get(
                "completion_tokens", 0
            )
            if isinstance(prompt_tokens, dict):
                prompt_tokens = 0
            if isinstance(completion_tokens, dict):
                completion_tokens = 0

            estimated = _compute_call_cost(
                provider, None, int(prompt_tokens or 0), int(completion_tokens or 0)
            )
            if estimated is not None:
                cost = estimated
                calls_estimated += 1
            else:
                cost = 0.0
                calls_unknown += 1

        by_provider[provider] += cost
        by_role[task_type] += cost

        day = ts[:10]
        daily[day]["cost"] += cost
        daily[day]["calls"] += 1

    total = round(sum(by_provider.values()), 6)

    daily_list = sorted(
        [
            {"date": d, "cost": round(v["cost"], 6), "calls": v["calls"]}
            for d, v in daily.items()
        ],
        key=lambda x: x["date"],
    )

    return {
        "total_cost": total,
        "by_provider": {
            k: round(v, 6)
            for k, v in sorted(
                by_provider.items(), key=lambda kv: kv[1], reverse=True
            )
        },
        "by_role": {
            k: round(v, 6)
            for k, v in sorted(by_role.items(), key=lambda kv: kv[1], reverse=True)
        },
        "daily": daily_list,
        "period_days": days,
        "calls_with_cost": calls_with_cost,
        "calls_estimated": calls_estimated,
        "calls_unknown": calls_unknown,
    }


def get_provider_cost_detail(provider: str, days: int = 30) -> dict:
    """Return detailed cost breakdown for a single provider.

    Returns:
        {
            "provider": str,
            "total_cost": float,
            "by_role": {task_type: cost, ...},
            "daily": [{date, cost, calls}, ...],
            "recent_calls": [{timestamp, task_type, cost, duration_ms, success}, ...],
            "pricing": dict or None,
        }
    """
    history = _load_history()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    by_role = defaultdict(float)
    daily = defaultdict(lambda: {"cost": 0.0, "calls": 0})
    recent_calls = []

    for entry in history:
        if entry.get("provider") != provider:
            continue
        ts = entry.get("timestamp", "")
        if ts < cutoff:
            continue

        task_type = entry.get("task_type", "unknown")
        recorded_cost = entry.get("cost")
        if isinstance(recorded_cost, (int, float)) and not isinstance(
            recorded_cost, bool
        ):
            cost = float(recorded_cost)
        else:
            estimated = _compute_call_cost(
                provider,
                None,
                int(entry.get("usage", {}).get("prompt_tokens", 0) or 0),
                int(entry.get("usage", {}).get("completion_tokens", 0) or 0),
            )
            cost = estimated if estimated is not None else 0.0

        by_role[task_type] += cost

        day = ts[:10]
        daily[day]["cost"] += cost
        daily[day]["calls"] += 1

        recent_calls.append(
            {
                "timestamp": ts,
                "task_type": task_type,
                "cost": round(cost, 8),
                "duration_ms": entry.get("duration_ms"),
                "success": entry.get("success", False),
            }
        )

    # Keep most recent 100 calls
    recent_calls.sort(key=lambda c: c["timestamp"], reverse=True)
    recent_calls = recent_calls[:100]

    total = round(sum(by_role.values()), 6)

    return {
        "provider": provider,
        "total_cost": total,
        "by_role": {
            k: round(v, 6)
            for k, v in sorted(by_role.items(), key=lambda kv: kv[1], reverse=True)
        },
        "daily": sorted(
            [
                {"date": d, "cost": round(v["cost"], 6), "calls": v["calls"]}
                for d, v in daily.items()
            ],
            key=lambda x: x["date"],
        ),
        "recent_calls": recent_calls,
        "pricing": get_pricing(provider),
    }


def get_daily_trend(days: int = 14) -> list[dict]:
    """Return daily cost trend for the last N days."""
    summary = get_cost_summary(days=days)
    return summary["daily"]


def get_monthly_summary(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    """Return cost summary for a specific month (defaults to current).

    Returns:
        {
            "year": int,
            "month": int,
            "total_cost": float,
            "by_provider": {provider: cost, ...},
            "by_role": {task_type: cost, ...},
            "daily": [{date, cost, calls}, ...],
        }
    """
    if year is None:
        year = date.today().year
    if month is None:
        month = date.today().month

    month_start = f"{year}-{month:02d}-01"
    if month == 12:
        month_end = f"{year + 1}-01-01"
    else:
        month_end = f"{year}-{month + 1:02d}-01"

    history = _load_history()
    by_provider = defaultdict(float)
    by_role = defaultdict(float)
    daily = defaultdict(lambda: {"cost": 0.0, "calls": 0})

    for entry in history:
        ts = entry.get("timestamp", "")
        if ts < month_start or ts >= month_end:
            continue

        provider = entry.get("provider", "unknown")
        task_type = entry.get("task_type", "unknown")
        recorded_cost = entry.get("cost")

        if isinstance(recorded_cost, (int, float)) and not isinstance(
            recorded_cost, bool
        ):
            cost = float(recorded_cost)
        else:
            estimated = _compute_call_cost(
                provider,
                None,
                int(entry.get("usage", {}).get("prompt_tokens", 0) or 0),
                int(entry.get("usage", {}).get("completion_tokens", 0) or 0),
            )
            cost = estimated if estimated is not None else 0.0

        by_provider[provider] += cost
        by_role[task_type] += cost
        day = ts[:10]
        daily[day]["cost"] += cost
        daily[day]["calls"] += 1

    total = round(sum(by_provider.values()), 6)

    return {
        "year": year,
        "month": month,
        "total_cost": total,
        "by_provider": {
            k: round(v, 6)
            for k, v in sorted(
                by_provider.items(), key=lambda kv: kv[1], reverse=True
            )
        },
        "by_role": {
            k: round(v, 6)
            for k, v in sorted(by_role.items(), key=lambda kv: kv[1], reverse=True)
        },
        "daily": sorted(
            [
                {"date": d, "cost": round(v["cost"], 6), "calls": v["calls"]}
                for d, v in daily.items()
            ],
            key=lambda x: x["date"],
        ),
    }


def get_cost_export(days: int = 30) -> list[dict]:
    """Return flat cost records for CSV/JSON export.

    Each record includes computed cost (recorded or estimated), provider,
    task_type, timestamp, duration_ms, and success status.
    """
    history = _load_history()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    records: list[dict] = []
    for entry in history:
        ts = entry.get("timestamp", "")
        if ts < cutoff:
            continue

        provider = entry.get("provider", "unknown")
        task_type = entry.get("task_type", "unknown")
        recorded_cost = entry.get("cost")

        if isinstance(recorded_cost, (int, float)) and not isinstance(
            recorded_cost, bool
        ):
            cost = float(recorded_cost)
            cost_source = "recorded"
        else:
            prompt_tokens = entry.get("usage", {}).get("prompt_tokens", 0)
            completion_tokens = entry.get("usage", {}).get("completion_tokens", 0)
            if isinstance(prompt_tokens, dict):
                prompt_tokens = 0
            if isinstance(completion_tokens, dict):
                completion_tokens = 0

            estimated = _compute_call_cost(
                provider, None, int(prompt_tokens or 0), int(completion_tokens or 0)
            )
            if estimated is not None:
                cost = estimated
                cost_source = "estimated"
            else:
                cost = 0.0
                cost_source = "unknown"

        records.append({
            "timestamp": ts,
            "provider": provider,
            "task_type": task_type,
            "cost_usd": round(cost, 8),
            "cost_source": cost_source,
            "duration_ms": entry.get("duration_ms"),
            "success": entry.get("success", False),
            "description": entry.get("description", ""),
        })

    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return records
