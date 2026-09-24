"""Juris Kai plan entitlements — the single source of truth for features/quota.

Every plan tier maps to a set of *features* (what a user may do) and *quotas*
(how much per day/month). The bot and the Command Center both read this module,
so an operator edit to the editable pricing store (which is the runtime source
of ``features`` and the query/document quotas) is reflected identically on both
surfaces — they can never disagree.

This module is deliberately dependency-free at import time. It reads the live,
pricing-applied tiers from :mod:`core.juris_kai.accounts` lazily (the same dict
the bot already uses), falling back to the canonical defaults below when the
account store is not importable (e.g. a pure unit test of the matrix).

Security: NO imports of ``core.build_manager``, ``core.approval`` or
``core.deployment_manager``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Feature registry — the canonical vocabulary of things a plan can grant.
# ``upgrade_hint`` completes the sentence "<Feature label> <hint>."
# ---------------------------------------------------------------------------

FEATURE_REGISTRY: Dict[str, Dict[str, str]] = {
    "basic_legal_qa": {"label": "Legal Q&A", "upgrade_hint": "is available on every plan"},
    "case_lookup": {"label": "Case lookup", "upgrade_hint": "is available on every plan"},
    "everyday_law": {"label": "Everyday Law", "upgrade_hint": "is available on every plan"},
    "document_analysis": {"label": "Document analysis", "upgrade_hint": "requires a paid plan"},
    "legal_research": {"label": "Legal research", "upgrade_hint": "requires a paid plan"},
    "argument_construction": {"label": "Legal argument construction", "upgrade_hint": "requires a Basic or Professional plan"},
    "flashcards": {"label": "Flashcards", "upgrade_hint": "requires the Student or Professional plan"},
    "practice_tools": {"label": "Practice tools", "upgrade_hint": "requires the Student or Professional plan"},
    "mock_exams": {"label": "Mock exams", "upgrade_hint": "requires the Student or Professional plan"},
    "revision_notes": {"label": "Revision notes", "upgrade_hint": "requires the Student or Professional plan"},
    "learn_tools": {"label": "Guided learning tools", "upgrade_hint": "requires the Student or Professional plan"},
    "deep_research": {"label": "Deep Research", "upgrade_hint": "requires a paid plan"},
    "priority_responses": {"label": "Priority responses", "upgrade_hint": "requires the Professional plan"},
    "export_reports": {"label": "Report export", "upgrade_hint": "requires the Professional plan"},
    "api_access": {"label": "API access", "upgrade_hint": "requires the Professional Annual or Institution plan"},
    "institutional_seats": {"label": "Institutional seats", "upgrade_hint": "requires an Institution plan"},
}

#: Canonical default feature set per plan (used before pricing is applied).
DEFAULT_PLAN_FEATURES: Dict[str, List[str]] = {
    "free_trial": ["basic_legal_qa", "case_lookup", "everyday_law",
                   "document_analysis", "deep_research"],
    "student": [
        "basic_legal_qa", "case_lookup", "everyday_law", "document_analysis",
        "legal_research", "flashcards", "practice_tools", "mock_exams",
        "revision_notes", "learn_tools", "deep_research",
    ],
    "monthly_basic": [
        "basic_legal_qa", "case_lookup", "everyday_law", "document_analysis",
        "legal_research",
    ],
    "monthly_pro": [
        "basic_legal_qa", "case_lookup", "everyday_law", "document_analysis",
        "legal_research", "argument_construction", "flashcards",
        "practice_tools", "mock_exams", "revision_notes", "learn_tools",
        "deep_research", "priority_responses", "export_reports",
    ],
    "annual_pro": [
        "basic_legal_qa", "case_lookup", "everyday_law", "document_analysis",
        "legal_research", "argument_construction", "flashcards",
        "practice_tools", "mock_exams", "revision_notes", "learn_tools",
        "deep_research", "priority_responses", "export_reports", "api_access",
    ],
    "institution": [
        "basic_legal_qa", "case_lookup", "everyday_law", "document_analysis",
        "legal_research", "argument_construction", "flashcards",
        "practice_tools", "mock_exams", "revision_notes", "learn_tools",
        "deep_research", "priority_responses", "export_reports", "api_access",
        "institutional_seats",
    ],
}

#: Canonical default quotas per plan. ``queries_per_day`` and
#: ``documents_per_month`` mirror the pricing store; ``deep_research_per_day``
#: is defined here (pricing does not carry it).
DEFAULT_PLAN_QUOTAS: Dict[str, Dict[str, int]] = {
    "free_trial": {"queries_per_day": 20, "documents_per_month": 3,
                   "deep_research_per_day": 1},
    "student": {"queries_per_day": 150, "documents_per_month": 10,
                "deep_research_per_day": 5},
    "monthly_basic": {"queries_per_day": 100, "documents_per_month": 15,
                      "deep_research_per_day": 5},
    "monthly_pro": {"queries_per_day": 500, "documents_per_month": 50,
                    "deep_research_per_day": 50},
    "annual_pro": {"queries_per_day": 500, "documents_per_month": 50,
                   "deep_research_per_day": 50},
    "institution": {"queries_per_day": 1000, "documents_per_month": 200,
                    "deep_research_per_day": 100},
}

QUOTA_KEYS = ("queries_per_day", "documents_per_month", "deep_research_per_day")

#: Tiers that are "free" for the purposes of the sponsor slot.
FREE_TIER_KEYS = {"free_trial", "free"}

DEFAULT_FREE_TIER = "free_trial"


def default_features(tier_key: str) -> List[str]:
    """The canonical default feature list for a plan (a fresh copy)."""
    return list(DEFAULT_PLAN_FEATURES.get(str(tier_key), []))


def default_quota(tier_key: str, quota_key: str) -> int:
    """The canonical default for one quota of one plan."""
    return int(DEFAULT_PLAN_QUOTAS.get(str(tier_key), {}).get(quota_key, 0))


def _live_tier(tier_key: str) -> Optional[Dict[str, Any]]:
    """The pricing-applied tier from the account store, if importable."""
    try:
        from core.juris_kai import accounts
        tier = accounts.SUBSCRIPTION_TIERS.get(str(tier_key))
        return dict(tier) if isinstance(tier, dict) else None
    except Exception:  # noqa: BLE001 - matrix must work standalone
        return None


def features_for(tier_key: str) -> List[str]:
    """Features granted by a plan (live pricing store, else defaults)."""
    tier = _live_tier(tier_key)
    if tier is not None and isinstance(tier.get("features"), list):
        return [str(f) for f in tier["features"]]
    return default_features(tier_key)


def has_feature(tier_key: str, feature: str) -> bool:
    """Whether a plan grants a feature. Unknown plan/feature => False."""
    if not feature:
        return False
    return str(feature) in features_for(tier_key)


def quota(tier_key: str, quota_key: str) -> int:
    """The effective quota for a plan (live pricing store, else defaults)."""
    tier = _live_tier(tier_key)
    if tier is not None:
        field = {
            "queries_per_day": "max_queries_per_day",
            "documents_per_month": "max_documents_per_month",
            "deep_research_per_day": "max_deep_research_per_day",
        }.get(quota_key)
        if field and tier.get(field) is not None:
            try:
                return int(tier[field])
            except (TypeError, ValueError):
                pass
    return default_quota(tier_key, quota_key)


def entitlements_for(tier_key: str) -> Dict[str, Any]:
    """The full entitlement record for a plan."""
    plan = str(tier_key or DEFAULT_FREE_TIER)
    return {
        "plan": plan,
        "features": features_for(plan),
        "quotas": {k: quota(plan, k) for k in QUOTA_KEYS},
    }


def plans() -> List[str]:
    """Every known plan key (defaults ∪ live pricing tiers)."""
    keys = set(DEFAULT_PLAN_FEATURES)
    try:
        from core.juris_kai import accounts
        keys.update(str(k) for k in accounts.SUBSCRIPTION_TIERS)
    except Exception:  # noqa: BLE001
        pass
    return sorted(keys)


def matrix() -> Dict[str, Dict[str, Any]]:
    """The full plan → features/quotas matrix (for the CC Pricing tab)."""
    return {plan: entitlements_for(plan) for plan in plans()}


def is_free_tier(tier_key: str) -> bool:
    return str(tier_key or DEFAULT_FREE_TIER) in FREE_TIER_KEYS


def upgrade_prompt(feature: str, tier_key: str = "") -> str:
    """A clear, non-crashing upgrade prompt for a feature the plan lacks."""
    meta = FEATURE_REGISTRY.get(str(feature), {})
    label = meta.get("label") or str(feature or "That feature").replace("_", " ")
    hint = meta.get("upgrade_hint") or "requires a higher plan"
    return f"⚠️ {label} {hint}.\nUpgrade with /subscribe"


def check_feature(mgr: Any, account_id: str, feature: str) -> Optional[str]:
    """Return an upgrade prompt if the account's plan lacks ``feature``, else None.

    An unknown account is treated as free (denied), never raised.
    """
    tier = DEFAULT_FREE_TIER
    try:
        sub = mgr.get_active_subscription(account_id)
        if sub and sub.get("tier"):
            tier = str(sub["tier"])
    except Exception:  # noqa: BLE001 - a bad lookup must not crash a handler
        tier = DEFAULT_FREE_TIER
    if has_feature(tier, feature):
        return None
    return upgrade_prompt(feature, tier)
