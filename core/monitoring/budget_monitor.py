"""Phase 16D: budget-alerting layer atop 13W's real cost aggregates.

Checks actual spend (read from ai_usage_history.json and
get_provider_dashboard()) against configurable daily / monthly ceilings
and sends a Telegram alert (via 13Z's bridge) when a threshold is crossed.

Idempotency: an alert fires exactly once per (alert_type, period) pair --
every subsequent check within the same period is silent until the period
resets (the next day or the next calendar month).  Config is read from
``config/budget.json``; alert state lives in ``memory/ai_budget_state.json``.

Policy: alert-only -- no automatic provider-disabling, spend-limiting, or
rate-limiting.  Stop/restrict decisions stay human.
"""

import json
from datetime import datetime, date
from pathlib import Path

import core.ai.ai_router as ai_router
from core.telegram_bridge import send_message
from core.logger import info


_BUDGET_STATE_FILE = "ai_budget_state.json"
_BUDGET_CONFIG_PATH = Path("config/budget.json")


def _load_config():
    try:
        raw = _BUDGET_CONFIG_PATH.read_text()
        return json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "daily_limit_usd": None, "monthly_limit_usd": None, "per_provider_limits": {}}


def _load_state():
    path = Path("memory") / _BUDGET_STATE_FILE
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    path = Path("memory") / _BUDGET_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _daily_period():
    return date.today().isoformat()


def _monthly_period():
    return date.today().strftime("%Y-%m")


def _alert_key(category, provider=None):
    base = f"{category}:{provider}" if provider else f"{category}:total"
    return base


def _should_alert(alert_key, period, state):
    return state.get(alert_key) != period


def _record_alert(alert_key, period, state):
    state[alert_key] = period


def _compute_daily_usage(history):
    today_str = date.today().isoformat()
    costs = {}
    for entry in history:
        ts = entry.get("timestamp", "")
        if not ts.startswith(today_str):
            continue
        provider = entry["provider"]
        cost = entry.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs[provider] = costs.get(provider, 0.0) + cost
    return costs


def _compute_monthly_usage(history):
    today_str = date.today().strftime("%Y-%m")
    costs = {}
    for entry in history:
        ts = entry.get("timestamp", "")
        if not ts.startswith(today_str):
            continue
        provider = entry["provider"]
        cost = entry.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs[provider] = costs.get(provider, 0.0) + cost
    return costs


def _format_provider_breakdown(costs):
    if not costs:
        return "  (no cost-carrying calls recorded)"
    sorted_providers = sorted(costs.items(), key=lambda kv: kv[1], reverse=True)
    lines = []
    for provider, cost in sorted_providers:
        lines.append(f"  {provider}: ${cost:.2f}")
    return "\n".join(lines)


def _build_alert_message(category, limit, current, provider, breakdown):
    label = provider if provider else "Total"
    overage = current - limit

    lines = [
        "\U0001f6a8 **AI Budget Alert**",
        f"{category.capitalize()} limit exceeded!",
        f"**{label} Spend:** ${current:.2f} / ${limit:.2f} limit (${overage:.2f} over)",
        f"**Breakdown:**",
        breakdown,
        "*Action required: Manual review needed. No providers have been disabled.*",
    ]
    return "\n".join(lines)


def _check_category(category, limit, costs, state, period):
    dirty = False

    if limit is None:
        return dirty

    current = sum(costs.values())
    if current <= limit:
        return dirty

    key = _alert_key(category)
    if not _should_alert(key, period, state):
        return dirty

    breakdown = _format_provider_breakdown(costs)
    message = _build_alert_message(category, limit, current, None, breakdown)

    try:
        send_message(message)
    except Exception as error:
        info(f"budget alert (send_message) failed: {type(error).__name__}")
        return dirty

    _record_alert(key, period, state)
    return True


def _check_per_provider_limits(per_provider_limits, daily_costs, state, daily_period):
    dirty = False

    if not per_provider_limits:
        return dirty

    for provider, limits in per_provider_limits.items():
        daily_limit = limits.get("daily")
        if daily_limit is None:
            continue

        current = daily_costs.get(provider, 0.0)
        if current <= daily_limit:
            continue

        key = _alert_key("daily", provider)
        if not _should_alert(key, daily_period, state):
            continue

        breakdown = _format_provider_breakdown({provider: current})
        message = _build_alert_message("daily", daily_limit, current, provider, breakdown)

        try:
            send_message(message)
        except Exception as error:
            info(f"budget alert (send_message) for {provider} failed: {type(error).__name__}")
            continue

        _record_alert(key, daily_period, state)
        dirty = True

    return dirty


def check_budgets():
    config = _load_config()
    if not config.get("enabled", False):
        return

    history = ai_router.get_usage_history()
    if not history:
        return

    daily_costs = _compute_daily_usage(history)
    monthly_costs = _compute_monthly_usage(history)

    state = _load_state()
    daily_period = _daily_period()
    monthly_period = _monthly_period()
    dirty = False

    daily_limit = config.get("daily_limit_usd")
    monthly_limit = config.get("monthly_limit_usd")

    dirty = _check_category("daily", daily_limit, daily_costs, state, daily_period) or dirty
    dirty = _check_category("monthly", monthly_limit, monthly_costs, state, monthly_period) or dirty
    dirty = _check_per_provider_limits(
        config.get("per_provider_limits", {}), daily_costs, state, daily_period
    ) or dirty

    if dirty:
        _save_state(state)
