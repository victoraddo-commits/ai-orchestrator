"""Juris Kai subscription plans — local tier ↔ Paystack Plan linkage.

Each paid tier in the editable pricing store is backed by a real Paystack
**Plan**. This module keeps an idempotent local map (tier key → ``plan_code``)
and can create/update the remote plans via ``core.payments.paystack``:

  * match an existing plan by our stored code, else by the deterministic name;
  * create a plan when missing;
  * update ``amount``/``interval`` when the tier price/period changes.

The mapping is persisted to ``memory/juris_plans.json`` (per-mode, because live
and test plan codes are distinct). Keys are never logged or persisted.

Security: NO imports of ``core.build_manager``, ``core.approval`` or
``core.deployment_manager``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("juris_kai.plans")

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = str(_REPO_ROOT / "memory" / "juris_plans.json")
PLANS_VERSION = 1
CURRENCY = "GHS"

#: Local tier key → Paystack name prefix. Kept deterministic so a plan created
#: by a previous run can be found by name even if the local map is lost.
NAME_PREFIX = "Juris Kai"
DESCRIPTION_TAG = "kai-tier"


def plans_path() -> str:
    return os.environ.get("JURIS_PLANS_PATH", "") or DEFAULT_PATH


def plan_name(tier_key: str, tier: Dict[str, Any]) -> str:
    return f"{NAME_PREFIX} — {tier.get('name') or tier_key}"


def interval_for_tier(tier_key: str, tier: Dict[str, Any]) -> str:
    """Paystack interval for a local tier (monthly unless annual)."""
    key = (tier_key or "").lower()
    name = str(tier.get("name") or "").lower()
    try:
        days = int(tier.get("duration_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if "annual" in key or "annual" in name or "year" in key or days >= 365:
        return "annually"
    return "monthly"


def paid_tiers(tiers: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Return only the tiers that require payment (price > 0)."""
    if tiers is None:
        from core.juris_kai import pricing as _pricing
        tiers = _pricing.load_pricing().get("tiers") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, tier in tiers.items():
        try:
            price = float(tier.get("price_ghs") or 0)
        except (TypeError, ValueError):
            price = 0
        if price > 0:
            out[str(key)] = dict(tier)
    return out


def default_document(mode: str = "") -> Dict[str, Any]:
    return {
        "version": PLANS_VERSION,
        "mode": mode or "",
        "plans": {},
        "updated_at": None,
        "updated_by": None,
    }


def _load_raw(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or plans_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return default_document()
    except (OSError, ValueError):
        logger.warning("juris plans: unreadable map; starting empty")
        return default_document()
    if not isinstance(raw, dict):
        return default_document()
    return raw


def _migrate_by_mode(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return ``{mode: {tier: entry}}``, migrating a legacy flat map."""
    by_mode = raw.get("plans_by_mode")
    if isinstance(by_mode, dict):
        return {str(m): {str(k): dict(v) for k, v in (entries or {}).items()
                         if isinstance(v, dict)}
                for m, entries in by_mode.items()}
    legacy_mode = str(raw.get("mode") or "test")
    by_mode = {}
    for key, entry in (raw.get("plans") or {}).items():
        if isinstance(entry, dict):
            mode = str(entry.get("mode") or legacy_mode)
            by_mode.setdefault(mode, {})[str(key)] = dict(entry)
    return by_mode


def load_plan_map(mode: Optional[str] = None, path: Optional[str] = None) -> Dict[str, Any]:
    """Return the plan document.

    With ``mode`` given, only that mode's entries are returned (live and test
    plan codes are distinct). Without it, every mode's entries are merged so the
    Command Center can display plans created in any mode while checkout only
    ever attaches a code matching the active provider mode.
    """
    raw = _load_raw(path)
    by_mode = _migrate_by_mode(raw)
    current = mode or _current_mode()
    if mode:
        entries = by_mode.get(mode) or {}
        plans = {str(k): {**dict(v), "mode": mode} for k, v in entries.items()}
    else:
        plans = {}
        for entry_mode, entries in by_mode.items():
            for key, entry in (entries or {}).items():
                plans[str(key)] = {**dict(entry),
                                   "mode": entry.get("mode") or entry_mode}
    return {
        "version": PLANS_VERSION,
        "mode": current,
        "plans": plans,
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by"),
    }


def save_plan_map(plans: Dict[str, Any], *, mode: str = "",
                  updated_by: str = "", path: Optional[str] = None) -> Dict[str, Any]:
    path = path or plans_path()
    raw = _load_raw(path)
    by_mode = _migrate_by_mode(raw)
    md = mode or _current_mode() or "test"
    by_mode[md] = {str(k): dict(v) for k, v in plans.items()}
    doc = {
        "version": PLANS_VERSION,
        "plans_by_mode": by_mode,
        "mode": md,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by or None,
    }
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return doc


def plan_code_for_tier(tier_key: str, mode: Optional[str] = None) -> Optional[str]:
    """Return the stored Paystack plan code for a tier, if any.

    When ``mode`` is supplied the code is only returned if the stored entry was
    created in that same mode (live and test plan codes are never interchangeable).
    """
    doc = load_plan_map(mode=mode)
    entry = (doc.get("plans") or {}).get(str(tier_key)) or {}
    code = entry.get("plan_code")
    if not code:
        return None
    entry_mode = str(entry.get("mode") or "")
    if mode and entry_mode and entry_mode != mode:
        return None
    return str(code)


def tier_for_plan_code(plan_code: str, mode: Optional[str] = None) -> str:
    """Reverse lookup: Paystack ``plan_code`` → local tier key (or '')."""
    if not plan_code:
        return ""
    doc = load_plan_map(mode=mode)
    target = str(plan_code)
    for tier, entry in (doc.get("plans") or {}).items():
        if str((entry or {}).get("plan_code") or "") == target:
            return str(tier)
    return ""


def _current_mode() -> str:
    try:
        from core.payments import keys as _keys
        return _keys.mode()
    except Exception:
        return ""


def _name_matches(plan: Dict[str, Any], name: str, tier_key: str) -> bool:
    if (plan.get("name") or "") == name:
        return True
    desc = str(plan.get("description") or "")
    return f"{DESCRIPTION_TAG}:{tier_key}" in desc


def sync_plans(*, provider: Any = None, tiers: Optional[Dict[str, Any]] = None,
               updated_by: str = "") -> Dict[str, Any]:
    """Create/update a Paystack Plan for every paid tier (idempotent).

    Returns ``{"success", "mode", "created", "updated", "unchanged", "plans",
    "errors"}``. A per-tier remote failure is reported in ``errors`` and does
    not abort the remaining tiers.
    """
    from core.juris_kai.paystack_checkout import get_paystack_provider

    prov = provider or get_paystack_provider()
    mode = getattr(prov, "mode", "") or _current_mode()
    doc = load_plan_map(mode=mode)
    stored = doc.get("plans") or {}
    tiers = paid_tiers(tiers)

    try:
        remote = prov.list_plans()
    except Exception as exc:  # transport/misconfig — still try stored codes
        logger.warning("juris plans: could not list remote plans (%s)",
                       type(exc).__name__)
        remote = []
    by_code = {str(p.get("plan_code")): p for p in remote if p.get("plan_code")}
    by_name = {str(p.get("name")): p for p in remote if p.get("name")}

    result_plans: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    created = updated = unchanged = 0

    for key in sorted(tiers):
        tier = tiers[key]
        name = plan_name(key, tier)
        amount = int(round(float(tier.get("price_ghs") or 0) * 100))
        interval = interval_for_tier(key, tier)
        if amount <= 0:
            continue
        try:
            plan = None
            stored_code = (stored.get(key) or {}).get("plan_code")
            if stored_code and str(stored_code) in by_code:
                plan = by_code[str(stored_code)]
            else:
                for candidate in remote:
                    if _name_matches(candidate, name, key):
                        plan = candidate
                        break

            if plan is None:
                plan = prov.create_plan(
                    name=name, amount=amount, interval=interval,
                    currency=CURRENCY,
                    description=f"Kai subscription plan ({DESCRIPTION_TAG}:{key})",
                )
                created += 1
            else:
                need_update = (
                    int(plan.get("amount") or 0) != amount
                    or str(plan.get("interval") or "").lower() != interval
                )
                if need_update:
                    plan = prov.update_plan(
                        str(plan.get("plan_code")), amount=amount,
                        interval=interval, name=name)
                    updated += 1
                else:
                    unchanged += 1

            stored[key] = {
                "tier": key,
                "plan_code": plan.get("plan_code"),
                "plan_id": plan.get("id"),
                "name": plan.get("name", name),
                "amount_minor": int(plan.get("amount") or amount),
                "interval": plan.get("interval", interval),
                "currency": plan.get("currency", CURRENCY),
                "mode": mode,
            }
            result_plans.append(dict(stored[key]))
        except Exception as exc:  # noqa: BLE001 - report, keep syncing
            logger.warning("juris plans: tier %s sync failed (%s)", key,
                           type(exc).__name__)
            errors.append({"tier": key, "error": str(exc)})

    save_plan_map(stored, mode=mode, updated_by=updated_by)
    return {
        "success": not errors,
        "mode": mode,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "plans": result_plans,
        "errors": errors,
    }
