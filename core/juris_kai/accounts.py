"""Juris Kai Multi-Tenant Account Management.

SQLite-backed user account system for the paid, multi-tenant Juris Kai
Telegram bot. Each user gets their own isolated account — no shared accounts.

Security: NO imports from core.build_manager, core.approval, or
core.deployment_manager. This module operates entirely within the legal
assistant boundary.
"""

import json
import os
import sqlite3
import uuid
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger("juris_kai.accounts")

# Database location
DB_DIR = os.environ.get("JURIS_KAI_DB_DIR", str(Path(__file__).parent.parent.parent / "memory"))
DB_PATH = os.path.join(DB_DIR, "juris_kai_accounts.db")

# Subscription tiers
SUBSCRIPTION_TIERS = {
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

# Per-document billing rate (GHS per page)
PER_DOCUMENT_PAGE_RATE_GHS = 2.0

DISCLAIMER_TEXT = (
    "⚖️ *Welcome to Juris Kai!*\n\n"
    "I am a legal research assistant and tutor, *not a lawyer*. "
    "My responses are for educational and informational purposes only "
    "and do not constitute legal advice.\n\n"
    "For legal advice specific to your situation, please consult a "
    "qualified legal practitioner.\n\n"
    "By using this service, you acknowledge this disclaimer."
)


def _get_db() -> sqlite3.Connection:
    """Get or create the accounts database."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Initialize database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS juris_accounts (
            account_id TEXT PRIMARY KEY,
            telegram_id TEXT UNIQUE NOT NULL,
            full_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            subscription_tier TEXT NOT NULL DEFAULT 'free_trial',
            subscription_start TEXT,
            subscription_end TEXT,
            queries_today INTEGER DEFAULT 0,
            queries_date TEXT,
            documents_this_month INTEGER DEFAULT 0,
            documents_month TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1,
            disclaimer_accepted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS juris_payments (
            payment_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            amount_ghs REAL NOT NULL,
            payment_type TEXT NOT NULL,
            hubtel_transaction_id TEXT,
            hubtel_status TEXT DEFAULT 'pending',
            subscription_tier TEXT,
            document_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (account_id) REFERENCES juris_accounts(account_id)
        );

        CREATE TABLE IF NOT EXISTS juris_document_analyses (
            analysis_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            document_name TEXT NOT NULL,
            page_count INTEGER DEFAULT 1,
            cost_ghs REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            result_summary TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES juris_accounts(account_id)
        );

        CREATE TABLE IF NOT EXISTS juris_usage_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES juris_accounts(account_id)
        );

        -- Phase 4: Security & audit tables
        CREATE TABLE IF NOT EXISTS juris_security_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            event_type TEXT NOT NULL,   -- auth_failed, rate_limited, suspicious_activity, admin_denied
            details TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS juris_rate_limits (
            telegram_id TEXT PRIMARY KEY,
            window_start REAL NOT NULL,
            message_count INTEGER DEFAULT 1,
            last_updated TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_juris_telegram
            ON juris_accounts(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_juris_payments_account
            ON juris_payments(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_docs_account
            ON juris_document_analyses(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_usage_account
            ON juris_usage_log(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_security_telegram
            ON juris_security_log(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_juris_security_event
            ON juris_security_log(event_type);
    """)


class AccountManager:
    """Manages multi-tenant Juris Kai accounts."""

    def __init__(self):
        self.db = _get_db()

    # ---- Account CRUD ----

    def get_or_create(self, telegram_id: str, full_name: str = "") -> Dict[str, Any]:
        """Get existing account or create a new trial account for a Telegram user.

        Returns dict with account data + 'is_new' flag.
        """
        row = self.db.execute(
            "SELECT * FROM juris_accounts WHERE telegram_id = ?",
            (str(telegram_id),),
        ).fetchone()

        if row:
            return {**dict(row), "is_new": False}

        # Create new free trial account
        account_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        trial_end = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        self.db.execute(
            """INSERT INTO juris_accounts
               (account_id, telegram_id, full_name, subscription_tier,
                subscription_start, subscription_end, created_at, updated_at)
               VALUES (?, ?, ?, 'free_trial', ?, ?, ?, ?)""",
            (account_id, str(telegram_id), full_name, now, trial_end, now, now),
        )
        self.db.commit()

        logger.info(f"New Juris Kai account: {account_id} for telegram_id={telegram_id}")
        result = self.get_account(account_id)
        if result:
            result["is_new"] = True
        return result

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get account by ID."""
        row = self.db.execute(
            "SELECT * FROM juris_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_telegram(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Get account by Telegram ID."""
        row = self.db.execute(
            "SELECT * FROM juris_accounts WHERE telegram_id = ?",
            (str(telegram_id),),
        ).fetchone()
        return dict(row) if row else None

    def accept_disclaimer(self, account_id: str) -> bool:
        """Mark disclaimer as accepted."""
        self.db.execute(
            "UPDATE juris_accounts SET disclaimer_accepted = 1, "
            "updated_at = datetime('now') WHERE account_id = ?",
            (account_id,),
        )
        self.db.commit()
        return True

    def update_profile(self, account_id: str, **fields) -> bool:
        """Update account profile fields (full_name, email, phone)."""
        allowed = {"full_name", "email", "phone"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        self.db.execute(
            f"UPDATE juris_accounts SET {set_clause}, updated_at = datetime('now') "
            f"WHERE account_id = ?",
            values,
        )
        self.db.commit()
        return True

    def deactivate(self, account_id: str) -> bool:
        """Deactivate an account."""
        self.db.execute(
            "UPDATE juris_accounts SET is_active = 0, updated_at = datetime('now') "
            "WHERE account_id = ?",
            (account_id,),
        )
        self.db.commit()
        return True

    # ---- Subscription ----

    def get_active_subscription(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get the current subscription status for an account."""
        account = self.get_account(account_id)
        if not account:
            return None

        tier = account["subscription_tier"]
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS["free_trial"])

        # Check if subscription has expired
        end_str = account.get("subscription_end")
        is_expired = False
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                is_expired = datetime.now(timezone.utc) > end_dt
            except (ValueError, TypeError):
                pass

        return {
            "tier": tier,
            "tier_name": tier_info["name"],
            "price_ghs": tier_info["price_ghs"],
            "start": account.get("subscription_start"),
            "end": account.get("subscription_end"),
            "is_expired": is_expired,
            "is_active": bool(account.get("is_active")) and not is_expired,
            "features": tier_info["features"],
            "limits": {
                "max_documents_per_month": tier_info["max_documents_per_month"],
                "max_queries_per_day": tier_info["max_queries_per_day"],
            },
        }

    def set_subscription(self, account_id: str, tier: str) -> bool:
        """Upgrade/downgrade subscription tier."""
        if tier not in SUBSCRIPTION_TIERS:
            return False

        tier_info = SUBSCRIPTION_TIERS[tier]
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=tier_info["duration_days"])

        self.db.execute(
            """UPDATE juris_accounts
               SET subscription_tier = ?, subscription_start = ?,
                   subscription_end = ?, updated_at = datetime('now')
               WHERE account_id = ?""",
            (tier, now.isoformat(), end.isoformat(), account_id),
        )
        self.db.commit()
        logger.info(f"Account {account_id} subscription updated to {tier}")
        return True

    # ---- Usage limits & quota ----

    def check_query_limit(self, account_id: str) -> Dict[str, Any]:
        """Check if user is within daily query limits. Returns {allowed, remaining, limit}."""
        sub = self.get_active_subscription(account_id)
        if not sub:
            return {"allowed": False, "remaining": 0, "limit": 0, "reason": "no_account"}

        if not sub["is_active"]:
            return {"allowed": False, "remaining": 0, "limit": 0, "reason": "expired"}

        limit = sub["limits"]["max_queries_per_day"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        row = self.db.execute(
            "SELECT queries_today, queries_date FROM juris_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()

        if not row:
            return {"allowed": False, "remaining": 0, "limit": limit, "reason": "no_account"}

        if row["queries_date"] != today:
            # Reset for new day
            self.db.execute(
                "UPDATE juris_accounts SET queries_today = 0, queries_date = ? WHERE account_id = ?",
                (today, account_id),
            )
            self.db.commit()
            return {"allowed": True, "remaining": limit, "limit": limit}

        used = row["queries_today"]
        remaining = max(0, limit - used)
        return {"allowed": remaining > 0, "remaining": remaining, "limit": limit}

    def record_query(self, account_id: str) -> bool:
        """Record a query usage. Call after successful AI response."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.db.execute(
            """UPDATE juris_accounts
               SET queries_today = queries_today + 1, queries_date = ?,
                   updated_at = datetime('now')
               WHERE account_id = ?""",
            (today, account_id),
        )
        self.db.execute(
            "INSERT INTO juris_usage_log (account_id, action_type) VALUES (?, 'query')",
            (account_id,),
        )
        self.db.commit()
        return True

    def check_document_limit(self, account_id: str) -> Dict[str, Any]:
        """Check if user can upload more documents this month."""
        sub = self.get_active_subscription(account_id)
        if not sub or not sub["is_active"]:
            return {"allowed": False, "remaining": 0, "limit": 0, "reason": "expired"}

        limit = sub["limits"]["max_documents_per_month"]
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        row = self.db.execute(
            "SELECT documents_this_month, documents_month FROM juris_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()

        if not row:
            return {"allowed": False, "remaining": 0, "limit": limit}

        if row["documents_month"] != month:
            self.db.execute(
                "UPDATE juris_accounts SET documents_this_month = 0, documents_month = ? WHERE account_id = ?",
                (month, account_id),
            )
            self.db.commit()
            return {"allowed": True, "remaining": limit, "limit": limit}

        used = row["documents_this_month"]
        remaining = max(0, limit - used)
        return {"allowed": remaining > 0, "remaining": remaining, "limit": limit}

    # ---- Document billing ----

    def bill_document_analysis(self, account_id: str, document_name: str, page_count: int) -> Dict[str, Any]:
        """Bill for a document analysis. Returns cost and analysis_id."""
        cost = page_count * PER_DOCUMENT_PAGE_RATE_GHS
        analysis_id = str(uuid.uuid4())[:12]

        self.db.execute(
            """INSERT INTO juris_document_analyses
               (analysis_id, account_id, document_name, page_count, cost_ghs, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (analysis_id, account_id, document_name, page_count, cost),
        )

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        self.db.execute(
            """UPDATE juris_accounts
               SET documents_this_month = documents_this_month + 1,
                   documents_month = ?, updated_at = datetime('now')
               WHERE account_id = ?""",
            (month, account_id),
        )
        self.db.commit()

        return {
            "analysis_id": analysis_id,
            "document_name": document_name,
            "page_count": page_count,
            "cost_ghs": cost,
            "rate_per_page": PER_DOCUMENT_PAGE_RATE_GHS,
        }

    # ---- Admin / Dashboard ----

    def list_all_accounts(self) -> List[Dict[str, Any]]:
        """List all accounts (admin only)."""
        rows = self.db.execute(
            "SELECT * FROM juris_accounts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate stats for dashboard."""
        total = self.db.execute("SELECT COUNT(*) as c FROM juris_accounts").fetchone()
        active = self.db.execute(
            "SELECT COUNT(*) as c FROM juris_accounts WHERE is_active = 1"
        ).fetchone()
        by_tier = self.db.execute(
            "SELECT subscription_tier, COUNT(*) as c FROM juris_accounts "
            "WHERE is_active = 1 GROUP BY subscription_tier"
        ).fetchall()
        total_revenue = self.db.execute(
            "SELECT COALESCE(SUM(amount_ghs), 0) as c FROM juris_payments "
            "WHERE hubtel_status = 'completed'"
        ).fetchone()
        total_queries = self.db.execute(
            "SELECT COUNT(*) as c FROM juris_usage_log WHERE action_type = 'query'"
        ).fetchone()

        return {
            "total_accounts": total["c"] if total else 0,
            "active_accounts": active["c"] if active else 0,
            "by_tier": {r["subscription_tier"]: r["c"] for r in by_tier},
            "total_revenue_ghs": total_revenue["c"] if total_revenue else 0,
            "total_queries": total_queries["c"] if total_queries else 0,
        }

    # ---- Security / audit logging ----

    def log_security_event(self, telegram_id: str, event_type: str, details: str = "",
                           ip_address: str = "") -> bool:
        """Log a security-related event for audit and abuse detection."""
        self.db.execute(
            """INSERT INTO juris_security_log
               (telegram_id, event_type, details, ip_address)
               VALUES (?, ?, ?, ?)""",
            (str(telegram_id), event_type, details, ip_address),
        )
        self.db.commit()
        return True

    def get_security_logs(self, event_type: str = "", limit: int = 100) -> list[dict]:
        """Get recent security events, optionally filtered by type."""
        if event_type:
            rows = self.db.execute(
                "SELECT * FROM juris_security_log WHERE event_type = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM juris_security_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def check_abuse(self, telegram_id: str, max_events_per_hour: int = 30) -> bool:
        """Check if a user is exhibiting abusive behavior (excessive queries in 1h).
        Returns True if abuse is detected."""
        rows = self.db.execute(
            """SELECT COUNT(*) as c FROM juris_usage_log
               WHERE account_id IN (SELECT account_id FROM juris_accounts WHERE telegram_id = ?)
               AND created_at > datetime('now', '-1 hour')""",
            (str(telegram_id),),
        ).fetchone()
        if rows and rows["c"] > max_events_per_hour:
            self.log_security_event(telegram_id, "suspicious_activity",
                                    f"Exceeded hourly limit: {rows['c']} queries in 1h")
            return True
        return False

    def log_admin_denied(self, telegram_id: str, attempted_action: str = "") -> bool:
        """Log an unauthorized admin access attempt."""
        return self.log_security_event(telegram_id, "admin_denied",
                                       f"Attempted: {attempted_action}")

    def log_rate_limit_hit(self, telegram_id: str) -> bool:
        """Log when a user hits the rate limit."""
        return self.log_security_event(telegram_id, "rate_limited",
                                       "Message rate limit exceeded")


# Module-level convenience
_account_manager: Optional[AccountManager] = None


def get_account_manager() -> AccountManager:
    """Get or create the singleton AccountManager."""
    global _account_manager
    if _account_manager is None:
        _account_manager = AccountManager()
    return _account_manager
