"""Juris Kai pricing configuration store.

Subscription tiers and the per-document page rate are editable at runtime from
the Kai Command Center. They live in a small JSON document layered over the
hard-coded defaults so behaviour is identical until an operator edits it.

Security: NO imports of ``core.build_manager``, ``core.approval``, or
``core.deployment_manager``. This module operates entirely within the legal
assistant boundary.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("juris_kai.pricing")

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = str(_REPO_ROOT / "memory" / "juris_pricing.json")
PRICING_VERSION = 1

# Defaults MUST stay identical to the legacy hard-coded values in accounts.py.
DEFAULT_TIERS: Dict[str, Dict[str, Any]] = {
    "free_trial": {
        "name": "Free Trial",
        "duration_days": 7,
        "price_ghs": 0,
        "max_documents_per_month": 3,
        "max_queries_per_day": 20,
        "features": ["basic_legal_qa", "case_lookup"],
    },
    "monthly_basic": {
        "name": "Basic Monthly",
        "duration_days": 30,
        "price_ghs": 50,
        "max_documents_per_month": 15,
        "max_queries_per_day": 100,
        "features": ["basic_legal_qa", "case_lookup", "document_analysis", "legal_research"],
    },
    "monthly_pro": {
        "name": "Professional Monthly",
        "duration_days": 30,
        "price_ghs": 150,
        "max_documents_per_month": 50,
        "max_queries_per_day": 500,
        "features": [
            "basic_legal_qa", "case_lookup", "document_analysis",
            "legal_research", "argument_construction", "flashcards",
            "priority_responses", "export_reports",
        ],
    },
    "annual_pro": {
        "name": "Professional Annual",
        "duration_days": 365,
        "price_ghs": 1500,
        "max_documents_per_month": 50,
        "max_queries_per_day": 500,
        "features": [
            "basic_legal_qa", "case_lookup", "document_analysis",
            "legal_research", "argument_construction", "flashcards",
            "priority_responses", "export_reports", "api_access",
        ],
    },
}

DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS = 2.0


class PricingValidationError(ValueError):
    """Raised when a pricing document fails validation."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def pricing_path() -> str:
    """Return the active pricing file path (env override aware)."""
    return os.environ.get("JURIS_PRICING_PATH", "") or DEFAULT_PATH


def default_pricing() -> Dict[str, Any]:
    """Return a fresh copy of the default pricing document."""
    return {
        "version": PRICING_VERSION,
        "tiers": deepcopy(DEFAULT_TIERS),
        "per_document_page_rate_ghs": DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS,
        "updated_at": None,
        "updated_by": None,
    }


def _as_number(value: Any, field: str, errors: List[str]) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        errors.append(f"{field} must be a number")
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be a number")
        return None
    if num < 0:
        errors.append(f"{field} must be >= 0")
    return num


def _as_int(value: Any, field: str, errors: List[str], minimum: int = 0) -> Optional[int]:
    if isinstance(value, bool) or value is None or value == "":
        errors.append(f"{field} must be an integer")
        return None
    try:
        num = int(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be an integer")
        return None
    if num < minimum:
        errors.append(f"{field} must be >= {minimum}")
    return num


def validate_pricing(tiers: Any, per_page_rate: Any) -> List[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: List[str] = []
    if not isinstance(tiers, dict) or not tiers:
        errors.append("tiers must be a non-empty object")
        return errors

    for key, tier in tiers.items():
        if not isinstance(key, str) or not key.strip():
            errors.append("tier key must be a non-empty string")
            continue
        if not isinstance(tier, dict):
            errors.append(f"{key}: tier must be an object")
            continue
        if not str(tier.get("name") or "").strip():
            errors.append(f"{key}.name is required")
        _as_int(tier.get("duration_days"), f"{key}.duration_days", errors, minimum=1)
        _as_number(tier.get("price_ghs"), f"{key}.price_ghs", errors)
        _as_int(tier.get("max_documents_per_month"),
                f"{key}.max_documents_per_month", errors, minimum=0)
        _as_int(tier.get("max_queries_per_day"),
                f"{key}.max_queries_per_day", errors, minimum=0)
        features = tier.get("features")
        if (not isinstance(features, list)
                or any(not isinstance(f, str) or not f.strip() for f in features)):
            errors.append(f"{key}.features must be a list of non-empty strings")

    _as_number(per_page_rate, "per_document_page_rate_ghs", errors)
    return errors


def _clean_tier(tier: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(tier.get("name") or "").strip(),
        "duration_days": int(tier.get("duration_days") or 0),
        "price_ghs": round(float(tier.get("price_ghs") or 0), 2),
        "max_documents_per_month": int(tier.get("max_documents_per_month") or 0),
        "max_queries_per_day": int(tier.get("max_queries_per_day") or 0),
        "features": [str(f).strip() for f in (tier.get("features") or [])],
    }


def load_pricing(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the pricing document from disk, falling back to defaults.

    A missing, unreadable, or invalid file never raises — the safe defaults are
    returned and the reason is logged, so a bad edit cannot take the bot down.
    """
    path = path or pricing_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return default_pricing()
    except (OSError, ValueError) as exc:
        logger.warning("juris pricing: unreadable config (%s); using defaults",
                       type(exc).__name__)
        return default_pricing()

    if not isinstance(raw, dict):
        logger.warning("juris pricing: config is not an object; using defaults")
        return default_pricing()

    tiers = raw.get("tiers")
    rate = raw.get("per_document_page_rate_ghs",
                   DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS)
    errors = validate_pricing(tiers, rate)
    if errors:
        logger.warning("juris pricing: invalid config (%s); using defaults",
                       "; ".join(errors))
        return default_pricing()

    return {
        "version": PRICING_VERSION,
        "tiers": {str(k): _clean_tier(v) for k, v in tiers.items()},
        "per_document_page_rate_ghs": round(float(rate), 2),
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by"),
    }


def save_pricing(tiers: Any, per_page_rate: Any, updated_by: str = "",
                 path: Optional[str] = None) -> Dict[str, Any]:
    """Validate, persist, and apply a pricing document. Raises on invalid."""
    errors = validate_pricing(tiers, per_page_rate)
    if errors:
        raise PricingValidationError(errors)

    path = path or pricing_path()
    doc = {
        "version": PRICING_VERSION,
        "tiers": {str(k): _clean_tier(v) for k, v in tiers.items()},
        "per_document_page_rate_ghs": round(float(per_page_rate), 2),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by or None,
    }

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
    os.replace(tmp_path, path)

    apply_to_accounts(doc)
    return doc


def apply_to_accounts(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Apply a pricing document to the running account manager (in place)."""
    doc = doc if doc is not None else load_pricing()
    from core.juris_kai import accounts as _accounts

    _accounts.apply_pricing(doc)
    return doc
