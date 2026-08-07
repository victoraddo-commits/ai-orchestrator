"""Juris Kai Dashboard API Endpoints.

Provides management endpoints for the Juris Kai admin tab on the Kai Dashboard:
- Account listing, subscription management
- Billing and revenue overview
- Usage statistics
- Document analysis queue

Security: These endpoints require operator authentication (same pattern
as other management endpoints in core/api.py). Read-only for viewers,
full access for operators.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from core.juris_kai.accounts import get_account_manager, SUBSCRIPTION_TIERS
from core.juris_kai.payments import get_payment_client

logger = logging.getLogger("juris_kai.dashboard")


def get_dashboard_stats() -> Dict[str, Any]:
    """Get aggregate Juris Kai stats for the dashboard."""
    mgr = get_account_manager()
    stats = mgr.get_stats()
    return {
        "juris_kai": {
            **stats,
            "subscription_tiers": SUBSCRIPTION_TIERS,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }


def list_accounts(
    page: int = 1, per_page: int = 50, tier_filter: Optional[str] = None,
    active_only: bool = False,
) -> Dict[str, Any]:
    """List Juris Kai accounts with pagination and filtering."""
    mgr = get_account_manager()
    all_accounts = mgr.list_all_accounts()

    # Apply filters
    if active_only:
        all_accounts = [a for a in all_accounts if a.get("is_active")]
    if tier_filter:
        all_accounts = [a for a in all_accounts if a.get("subscription_tier") == tier_filter]

    total = len(all_accounts)
    start = (page - 1) * per_page
    page_accounts = all_accounts[start:start + per_page]

    # Enrich with subscription status
    enriched = []
    for acct in page_accounts:
        sub = mgr.get_active_subscription(acct["account_id"])
        enriched.append({**acct, "subscription": sub})

    return {
        "accounts": enriched,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def update_subscription(account_id: str, tier: str) -> Dict[str, Any]:
    """Admin: update an account's subscription tier."""
    if tier not in SUBSCRIPTION_TIERS:
        return {"success": False, "error": f"Unknown tier: {tier}"}

    mgr = get_account_manager()
    account = mgr.get_account(account_id)
    if not account:
        return {"success": False, "error": "Account not found"}

    mgr.set_subscription(account_id, tier)
    return {"success": True, "account_id": account_id, "new_tier": tier}


def get_account_detail(account_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed account information for admin view."""
    mgr = get_account_manager()
    account = mgr.get_account(account_id)
    if not account:
        return None

    sub = mgr.get_active_subscription(account_id)
    limit = mgr.check_query_limit(account_id)
    doc_limit = mgr.check_document_limit(account_id)

    return {
        **account,
        "subscription": sub,
        "usage": {
            "queries": limit,
            "documents": doc_limit,
        },
    }


def get_payment_history(account_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Get payment history, optionally filtered by account."""
    import sqlite3
    from core.juris_kai.accounts import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if account_id:
        rows = conn.execute(
            "SELECT * FROM juris_payments WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM juris_payments ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_usage_log(account_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Get usage log, optionally filtered by account."""
    import sqlite3
    from core.juris_kai.accounts import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if account_id:
        rows = conn.execute(
            "SELECT * FROM juris_usage_log WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM juris_usage_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]
