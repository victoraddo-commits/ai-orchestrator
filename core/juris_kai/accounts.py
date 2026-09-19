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

from core.juris_kai import pricing as _pricing

logger = logging.getLogger("juris_kai.accounts")

# Database location
DB_DIR = os.environ.get("JURIS_KAI_DB_DIR", str(Path(__file__).parent.parent.parent / "memory"))
DB_PATH = os.path.join(DB_DIR, "juris_kai_accounts.db")

# Subscription tiers — loaded from the editable pricing store. The module-level
# dict is mutated IN PLACE by apply_pricing() so every existing
# `from ...accounts import SUBSCRIPTION_TIERS` reference sees operator edits
# without a redeploy. Defaults live in core/juris_kai/pricing.py.
SUBSCRIPTION_TIERS: Dict[str, Dict[str, Any]] = {
    key: dict(value) for key, value in _pricing.DEFAULT_TIERS.items()
}

# Per-document billing rate (GHS per page)
PER_DOCUMENT_PAGE_RATE_GHS = _pricing.DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS


def apply_pricing(pricing_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Apply a pricing document (or reload it from disk) to the live values."""
    data = pricing_data if pricing_data is not None else _pricing.load_pricing()
    tiers = data.get("tiers") or {}
    SUBSCRIPTION_TIERS.clear()
    for key, tier in tiers.items():
        SUBSCRIPTION_TIERS[str(key)] = dict(tier)
    global PER_DOCUMENT_PAGE_RATE_GHS
    PER_DOCUMENT_PAGE_RATE_GHS = float(
        data.get("per_document_page_rate_ghs",
                 _pricing.DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS))
    logger.info("juris pricing applied: %d tier(s), page rate GHS %s",
                len(SUBSCRIPTION_TIERS), PER_DOCUMENT_PAGE_RATE_GHS)
    return data


def get_pricing() -> Dict[str, Any]:
    """Return the effective pricing (live tiers + per-document page rate)."""
    return {
        "tiers": {key: dict(value) for key, value in SUBSCRIPTION_TIERS.items()},
        "per_document_page_rate_ghs": PER_DOCUMENT_PAGE_RATE_GHS,
    }


apply_pricing()

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
    # check_same_thread=False is required because the AccountManager
    # singleton is shared between the scheduler thread and the FastAPI
    # worker threads. WAL mode + busy_timeout make this safe for our
    # read-heavy, low-contention workload.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
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

        CREATE TABLE IF NOT EXISTS juris_checkout_activations (
            reference TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            tier TEXT NOT NULL,
            provider TEXT DEFAULT 'paystack',
            amount_minor INTEGER DEFAULT 0,
            activated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            model TEXT DEFAULT '',
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

        -- Phase 5: Referral / invite system
        CREATE TABLE IF NOT EXISTS juris_referrals (
            referral_id TEXT PRIMARY KEY,
            inviter_account_id TEXT NOT NULL,
            invitee_telegram_id TEXT,
            invite_code TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'pending',
            reward_days INTEGER DEFAULT 0,
            invitee_trial_days INTEGER DEFAULT 3,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            accepted_at TEXT,
            FOREIGN KEY (inviter_account_id) REFERENCES juris_accounts(account_id)
        );

        -- Paystack subscription state (kept in sync from webhooks).
        CREATE TABLE IF NOT EXISTS juris_subscriptions (
            subscription_code TEXT PRIMARY KEY,
            account_id TEXT,
            tier TEXT DEFAULT '',
            plan_code TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            email_token TEXT DEFAULT '',
            customer_email TEXT DEFAULT '',
            amount_minor INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'GHS',
            next_payment_date TEXT,
            expires_at TEXT,
            raw TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Legal groups (admin- and user-created).
        CREATE TABLE IF NOT EXISTS legal_groups (
            group_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            invite_code TEXT UNIQUE,
            created_by TEXT DEFAULT '',
            created_by_kind TEXT DEFAULT 'user',  -- user | admin
            status TEXT NOT NULL DEFAULT 'active', -- active | archived
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS legal_group_members (
            group_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',   -- owner | admin | member
            added_by TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (group_id, account_id)
        );

        -- Every group mutation: actor, action, JSON details.
        CREATE TABLE IF NOT EXISTS legal_group_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            action TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Stored "audit the group's documents" reports.
        CREATE TABLE IF NOT EXISTS legal_group_reports (
            report_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            requested_by TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            document_count INTEGER DEFAULT 0,
            verified_count INTEGER DEFAULT 0,
            flagged_count INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            findings TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_juris_telegram
            ON juris_accounts(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_juris_payments_account
            ON juris_payments(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_docs_account
            ON juris_document_analyses(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_checkout_account
            ON juris_checkout_activations(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_usage_account
            ON juris_usage_log(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_security_telegram
            ON juris_security_log(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_juris_security_event
            ON juris_security_log(event_type);
        CREATE INDEX IF NOT EXISTS idx_juris_referrals_inviter
            ON juris_referrals(inviter_account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_referrals_code
            ON juris_referrals(invite_code);
        CREATE INDEX IF NOT EXISTS idx_juris_subscriptions_account
            ON juris_subscriptions(account_id);
        CREATE INDEX IF NOT EXISTS idx_juris_subscriptions_status
            ON juris_subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_legal_groups_status
            ON legal_groups(status);
        CREATE INDEX IF NOT EXISTS idx_legal_group_members_account
            ON legal_group_members(account_id);
        CREATE INDEX IF NOT EXISTS idx_legal_group_audit_group
            ON legal_group_audit(group_id);
        CREATE INDEX IF NOT EXISTS idx_legal_group_reports_group
            ON legal_group_reports(group_id);
    """)
    # Add token-tracking columns to existing usage_log tables (WI-14).
    # Safe to run on every init — ignores duplicates.
    for col, coldef in [
        ("input_tokens", "INTEGER DEFAULT 0"),
        ("output_tokens", "INTEGER DEFAULT 0"),
        ("model", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE juris_usage_log ADD COLUMN {col} {coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists


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

    # ---- Checkout activation (Paystack) ----

    def mark_checkout_activation(self, reference: str, account_id: str, tier: str,
                                  provider: str = "paystack",
                                  amount_minor: int = 0) -> bool:
        """Record a checkout reference as activated. Returns False if it was
        already used (idempotency guard for duplicate webhooks)."""
        cur = self.db.execute(
            """INSERT OR IGNORE INTO juris_checkout_activations
               (reference, account_id, tier, provider, amount_minor)
               VALUES (?, ?, ?, ?, ?)""",
            (str(reference), account_id, tier, provider, int(amount_minor or 0)),
        )
        self.db.commit()
        return cur.rowcount > 0

    def get_checkout_activation(self, reference: str) -> Optional[Dict[str, Any]]:
        """Look up a previously recorded checkout activation."""
        row = self.db.execute(
            "SELECT * FROM juris_checkout_activations WHERE reference = ?",
            (str(reference),),
        ).fetchone()
        return dict(row) if row else None

    def record_checkout_payment(self, reference: str, account_id: str,
                                 amount_ghs: float, tier: str,
                                 provider: str = "paystack",
                                 status: str = "completed") -> bool:
        """Record a completed subscription payment for the admin ledger."""
        cur = self.db.execute(
            """INSERT OR IGNORE INTO juris_payments
               (payment_id, account_id, amount_ghs, payment_type,
                hubtel_transaction_id, hubtel_status, subscription_tier, completed_at)
               VALUES (?, ?, ?, 'subscription', ?, ?, ?, datetime('now'))""",
            (str(reference), account_id, float(amount_ghs or 0),
             f"{provider}:{reference}", status, tier),
        )
        self.db.commit()
        return cur.rowcount > 0

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

    def try_record_query(self, account_id: str, input_tokens: int = 0,
                         output_tokens: int = 0, model: str = "") -> Dict[str, Any]:
        """Atomically check and record a query. Returns {allowed, remaining, limit}.

        Uses a single UPDATE that increments only if under the limit, then checks
        rows_affected to know whether the increment succeeded. Eliminates the
        check-then-increment race condition.
        """
        sub = self.get_active_subscription(account_id)
        if not sub or not sub["is_active"]:
            return {"allowed": False, "remaining": 0, "limit": 0, "reason": "inactive_account"}

        limit = sub["limits"]["max_queries_per_day"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Atomic conditional increment: only succeeds when queries_today < limit
        result = self.db.execute(
            """UPDATE juris_accounts
               SET queries_today = queries_today + 1, queries_date = ?,
                   updated_at = datetime('now')
               WHERE account_id = ?
                 AND (queries_date != ? OR queries_today < ?)
                 AND is_active = 1""",
            (today, account_id, today, limit),
        )
        rows_affected = result.rowcount
        self.db.execute(
            "INSERT INTO juris_usage_log (account_id, action_type, input_tokens,"
            " output_tokens, model) VALUES (?, 'query', ?, ?, ?)",
            (account_id, input_tokens, output_tokens, model),
        )
        self.db.commit()

        if rows_affected == 0:
            # Either at limit, inactive, or wrong date — do a clean check to report accurately
            row = self.db.execute(
                "SELECT queries_today, queries_date FROM juris_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row and row["queries_date"] != today:
                # Day rolled over mid-flight — retry once
                self.db.execute(
                    "UPDATE juris_accounts SET queries_today = 0, queries_date = ? WHERE account_id = ?",
                    (today, account_id),
                )
                self.db.commit()
                return {"allowed": True, "remaining": limit - 1, "limit": limit}
            return {"allowed": False, "remaining": 0, "limit": limit, "reason": "limit_exceeded"}

        remaining = max(0, limit - 1)
        return {"allowed": True, "remaining": remaining, "limit": limit}

    def record_query(self, account_id: str, input_tokens: int = 0,
                      output_tokens: int = 0, model: str = "") -> bool:
        """Record a query usage. Call after successful AI response.

        Deprecated: prefer try_record_query() for atomic check-and-record.
        This method bypasses the quota check (for already-authorized paths).
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.db.execute(
            """UPDATE juris_accounts
               SET queries_today = queries_today + 1, queries_date = ?,
                   updated_at = datetime('now')
               WHERE account_id = ?""",
            (today, account_id),
        )
        self.db.execute(
            "INSERT INTO juris_usage_log (account_id, action_type, input_tokens,"
            " output_tokens, model) VALUES (?, 'query', ?, ?, ?)",
            (account_id, input_tokens, output_tokens, model),
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

    # ---- Referral / invite system ----

    def generate_invite_code(self, account_id: str) -> Dict[str, Any]:
        """Generate a unique invite code for an account to share."""
        account = self.get_account(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}

        code = "JURIS-" + secrets.token_hex(4).upper()
        referral_id = str(uuid.uuid4())[:12]

        self.db.execute(
            """INSERT INTO juris_referrals
               (referral_id, inviter_account_id, invite_code, status, reward_days, invitee_trial_days)
               VALUES (?, ?, ?, 'pending', 1, 3)""",
            (referral_id, account_id, code),
        )
        self.db.commit()

        logger.info(f"Referral code {code} generated for account {account_id}")
        return {
            "success": True,
            "referral_id": referral_id,
            "invite_code": code,
            "inviter_account_id": account_id,
            "reward_days": 1,
            "invitee_trial_days": 3,
        }

    def accept_invite(self, code: str, invitee_telegram_id: str,
                      invitee_name: str = "") -> Dict[str, Any]:
        """Accept an invite code. Creates trial account for invitee and
        grants reward days to the inviter."""
        # Find the pending referral
        ref = self.db.execute(
            "SELECT * FROM juris_referrals WHERE invite_code = ? AND status = 'pending'",
            (code.upper(),),
        ).fetchone()

        if not ref:
            return {"success": False, "error": "Invalid or already used invite code"}

        # Check invitee doesn't already have an account (prevent self-referral farming)
        existing = self.get_by_telegram(invitee_telegram_id)
        if existing:
            # Already a user — still credit inviter if referral is pending
            pass

        # Create invitee's account with extended trial
        invitee_acct = self.get_or_create(invitee_telegram_id, invitee_name)
        if invitee_acct.get("is_new") or not existing:
            # Upgrade to referral trial (3 days instead of 7-day default trial)
            trial_end = (datetime.now(timezone.utc) + timedelta(days=ref["invitee_trial_days"])).isoformat()
            self.db.execute(
                """UPDATE juris_accounts
                   SET subscription_tier = 'free_trial',
                       subscription_start = datetime('now'),
                       subscription_end = ?,
                       updated_at = datetime('now')
                   WHERE account_id = ?""",
                (trial_end, invitee_acct["account_id"]),
            )

        # Grant reward days to inviter
        self._extend_subscription(ref["inviter_account_id"], ref["reward_days"])

        # Mark referral as accepted
        self.db.execute(
            """UPDATE juris_referrals
               SET status = 'accepted', invitee_telegram_id = ?,
                   accepted_at = datetime('now')
               WHERE referral_id = ?""",
            (str(invitee_telegram_id), ref["referral_id"]),
        )
        self.db.commit()

        # Log usage
        self.db.execute(
            "INSERT INTO juris_usage_log (account_id, action_type, details) VALUES (?, 'referral_accepted', ?)",
            (ref["inviter_account_id"], f"Invited telegram_id={invitee_telegram_id}"),
        )

        logger.info(f"Referral {ref['referral_id']} accepted: inviter={ref['inviter_account_id']}, "
                     f"invitee_telegram={invitee_telegram_id}, reward={ref['reward_days']}d")
        return {
            "success": True,
            "referral_id": ref["referral_id"],
            "inviter_reward_days": ref["reward_days"],
            "invitee_account_id": invitee_acct["account_id"],
            "invitee_trial_days": ref["invitee_trial_days"],
        }

    def get_referral_history(self, account_id: str) -> List[Dict[str, Any]]:
        """Get all referrals for an account."""
        rows = self.db.execute(
            "SELECT * FROM juris_referrals WHERE inviter_account_id = ? ORDER BY created_at DESC",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_referrals(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all referrals (admin view)."""
        rows = self.db.execute(
            "SELECT r.*, a.full_name as inviter_name, a.telegram_id as inviter_telegram "
            "FROM juris_referrals r LEFT JOIN juris_accounts a "
            "ON r.inviter_account_id = a.account_id "
            "ORDER BY r.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Admin: account management ----

    def grant_free_days(self, account_id: str, days: int,
                         reason: str = "admin_grant") -> Dict[str, Any]:
        """Extend an account's subscription by N days."""
        if days < 0:
            return {"success": False, "error": "Days must be non-negative"}

        account = self.get_account(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}

        self._extend_subscription(account_id, days)

        # Log
        self.db.execute(
            "INSERT INTO juris_usage_log (account_id, action_type, details) VALUES (?, ?, ?)",
            (account_id, "admin_grant_days", f"Granted {days} days: {reason}"),
        )
        self.db.commit()

        logger.info(f"Granted {days} free days to account {account_id}: {reason}")
        return {
            "success": True,
            "account_id": account_id,
            "days_granted": days,
            "new_end": self.get_account(account_id).get("subscription_end") if self.get_account(account_id) else None,
        }

    def _extend_subscription(self, account_id: str, days: int):
        """Internal: extend a subscription by N days from current end or now."""
        account = self.get_account(account_id)
        if not account:
            return

        end_str = account.get("subscription_end")
        if end_str:
            try:
                current_end = datetime.fromisoformat(end_str)
            except (ValueError, TypeError):
                current_end = datetime.now(timezone.utc)
        else:
            current_end = datetime.now(timezone.utc)

        # If already expired, start from now
        if current_end < datetime.now(timezone.utc):
            current_end = datetime.now(timezone.utc)

        new_end = current_end + timedelta(days=days)
        self.db.execute(
            """UPDATE juris_accounts
               SET subscription_end = ?, updated_at = datetime('now')
               WHERE account_id = ?""",
            (new_end.isoformat(), account_id),
        )

    def ban_account(self, account_id: str, reason: str = "") -> Dict[str, Any]:
        """Deactivate/ban an account."""
        account = self.get_account(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}

        self.db.execute(
            "UPDATE juris_accounts SET is_active = 0, updated_at = datetime('now') WHERE account_id = ?",
            (account_id,),
        )
        self.db.commit()
        self.log_security_event(
            account.get("telegram_id", ""), "account_banned",
            f"Account {account_id} banned: {reason}",
        )
        logger.info(f"Account {account_id} banned: {reason}")
        return {"success": True, "account_id": account_id, "action": "banned"}

    def unban_account(self, account_id: str) -> Dict[str, Any]:
        """Reactivate an account."""
        account = self.get_account(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}

        self.db.execute(
            "UPDATE juris_accounts SET is_active = 1, updated_at = datetime('now') WHERE account_id = ?",
            (account_id,),
        )
        self.db.commit()
        self.log_security_event(
            account.get("telegram_id", ""), "account_unbanned",
            f"Account {account_id} reactivated",
        )
        logger.info(f"Account {account_id} unbanned")
        return {"success": True, "account_id": account_id, "action": "unbanned"}

    def find_accounts(self, query: str = "", tier: str = "",
                       active_only: bool = False, page: int = 1,
                       per_page: int = 50) -> Dict[str, Any]:
        """Search accounts by telegram_id, name, email, or phone."""
        conditions = []
        params = []

        if query:
            conditions.append(
                "(telegram_id LIKE ? OR full_name LIKE ? OR email LIKE ? OR phone LIKE ?)"
            )
            q = f"%{query}%"
            params.extend([q, q, q, q])

        if tier:
            conditions.append("subscription_tier = ?")
            params.append(tier)

        if active_only:
            conditions.append("is_active = 1")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        total = self.db.execute(
            f"SELECT COUNT(*) as c FROM juris_accounts {where}", params,
        ).fetchone()

        offset = (page - 1) * per_page
        rows = self.db.execute(
            f"SELECT * FROM juris_accounts {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        # Enrich with subscription status
        enriched = []
        for r in rows:
            d = dict(r)
            d["subscription"] = self.get_active_subscription(d["account_id"])
            enriched.append(d)

        return {
            "accounts": enriched,
            "total": total["c"] if total else 0,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, ((total["c"] if total else 0) + per_page - 1) // per_page),
        }

    # ---- User administration (CC / admin-created accounts) ----

    def create_account(self, email: str = "", full_name: str = "",
                       tier: str = "free_trial", phone: str = "",
                       source: str = "cc") -> Dict[str, Any]:
        """Create an account directly, without a Telegram identity."""
        email = (email or "").strip()
        full_name = (full_name or "").strip()
        if "@" not in email:
            return {"success": False, "error": "a valid email is required"}
        if tier not in SUBSCRIPTION_TIERS:
            return {"success": False, "error": f"unknown tier: {tier}"}
        if self.find_by_email(email):
            return {"success": False,
                    "error": "an account with that email already exists"}
        account_id = str(uuid.uuid4())[:12]
        telegram_id = f"{source}-{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc)
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS["free_trial"])
        end = now + timedelta(days=int(tier_info.get("duration_days") or 0))
        self.db.execute(
            """INSERT INTO juris_accounts
               (account_id, telegram_id, full_name, email, phone,
                subscription_tier, subscription_start, subscription_end,
                created_at, updated_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (account_id, telegram_id, full_name, email, phone, tier,
             now.isoformat(), end.isoformat(), now.isoformat(), now.isoformat()),
        )
        self.db.commit()
        self.log_security_event(telegram_id, "admin_action",
                                f"account_created tier={tier} email={email}")
        logger.info("juris account created via %s: %s (%s)", source, account_id, tier)
        acct = self.get_account(account_id) or {}
        acct["success"] = True
        return acct

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        email = (email or "").strip().lower()
        if not email:
            return None
        row = self.db.execute(
            "SELECT * FROM juris_accounts WHERE lower(email) = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        return dict(row) if row else None

    def reactivate(self, account_id: str) -> bool:
        """Reactivate a deactivated account (alias of unban_account)."""
        return bool(self.unban_account(account_id).get("success"))

    # ---- Paystack subscription state ----

    def upsert_subscription(self, subscription_code: str, **fields) -> Dict[str, Any]:
        """Insert or update a subscription row by ``subscription_code``."""
        code = str(subscription_code or "").strip()
        if not code:
            return {}
        allowed = {"account_id", "tier", "plan_code", "status", "email_token",
                   "customer_email", "amount_minor", "currency",
                   "next_payment_date", "expires_at", "raw"}
        data = {k: v for k, v in fields.items() if k in allowed}
        raw = data.pop("raw", None)
        raw_json = raw if isinstance(raw, str) else json.dumps(raw or {})
        if self.get_subscription(code) is None:
            cols = ["subscription_code"] + list(data.keys()) + ["raw"]
            vals = [code] + list(data.values()) + [raw_json]
            placeholders = ", ".join(["?"] * len(cols))
            self.db.execute(
                f"INSERT INTO juris_subscriptions ({', '.join(cols)}) "
                f"VALUES ({placeholders})",
                vals,
            )
        else:
            sets = ", ".join(f"{k} = ?" for k in data)
            self.db.execute(
                f"UPDATE juris_subscriptions SET "
                f"{sets + ', ' if sets else ''}raw = ?, "
                "updated_at = datetime('now') WHERE subscription_code = ?",
                list(data.values()) + [raw_json, code],
            )
        self.db.commit()
        return self.get_subscription(code) or {}

    def get_subscription(self, subscription_code: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            "SELECT * FROM juris_subscriptions WHERE subscription_code = ?",
            (str(subscription_code),),
        ).fetchone()
        return dict(row) if row else None

    def list_subscriptions(self, limit: int = 100,
                           account_id: str = "") -> List[Dict[str, Any]]:
        if account_id:
            rows = self.db.execute(
                "SELECT * FROM juris_subscriptions WHERE account_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM juris_subscriptions ORDER BY updated_at DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def subscription_counts(self) -> Dict[str, Any]:
        total = self.db.execute(
            "SELECT COUNT(*) c FROM juris_subscriptions").fetchone()
        rows = self.db.execute(
            "SELECT status, COUNT(*) c FROM juris_subscriptions GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["c"] for r in rows}
        tier_rows = self.db.execute(
            "SELECT tier, COUNT(*) c FROM juris_subscriptions GROUP BY tier"
        ).fetchall()
        by_tier = {r["tier"]: r["c"] for r in tier_rows}
        active = int(by_status.get("active", 0) + by_status.get("non-renewing", 0))
        return {"total": total["c"] if total else 0, "by_status": by_status,
                "by_tier": by_tier, "active": active}

    def expire_subscription(self, account_id: str) -> bool:
        """Lapse an account's paid access (payment failure / cancellation)."""
        if not self.get_account(account_id):
            return False
        self.db.execute(
            "UPDATE juris_accounts SET subscription_end = ?, "
            "updated_at = datetime('now') WHERE account_id = ?",
            (datetime.now(timezone.utc).isoformat(), account_id),
        )
        self.db.commit()
        return True

    # ---- Legal groups ----

    def create_group(self, name: str, created_by: str = "",
                     created_by_kind: str = "user", description: str = "",
                     actor: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        if len(name) < 2:
            return {"success": False,
                    "error": "group name must be at least 2 characters"}
        group_id = str(uuid.uuid4())[:12]
        invite_code = "GRP-" + secrets.token_hex(4).upper()
        self.db.execute(
            """INSERT INTO legal_groups
               (group_id, name, description, invite_code, created_by,
                created_by_kind)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (group_id, name, description or "", invite_code,
             str(created_by or ""), created_by_kind or "user"),
        )
        if created_by:
            self.add_member(group_id, created_by, role="owner",
                            actor=actor or created_by)
        self.db.commit()
        self.log_group_audit(group_id, actor or created_by, "create",
                             {"name": name, "kind": created_by_kind})
        return {"success": True, "group": self.get_group(group_id)}

    def get_group(self, group_id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(group_id_or_code or "")
        if not key:
            return None
        row = self.db.execute(
            "SELECT * FROM legal_groups WHERE group_id = ? OR invite_code = ?",
            (key, key.upper()),
        ).fetchone()
        return dict(row) if row else None

    def list_groups(self, include_archived: bool = False,
                    account_id: str = "") -> List[Dict[str, Any]]:
        conds: List[str] = []
        params: List[Any] = []
        if not include_archived:
            conds.append("g.status = 'active'")
        if account_id:
            ids = [r["group_id"] for r in self.db.execute(
                "SELECT group_id FROM legal_group_members WHERE account_id = ?",
                (account_id,),
            ).fetchall()]
            if not ids:
                return []
            conds.append(f"g.group_id IN ({', '.join(['?'] * len(ids))})")
            params.extend(ids)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.db.execute(
            f"""SELECT g.*, (SELECT COUNT(*) FROM legal_group_members m
                             WHERE m.group_id = g.group_id) AS member_count
                FROM legal_groups g {where}
                ORDER BY g.created_at DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def rename_group(self, group_id: str, name: str,
                     actor: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        if len(name) < 2:
            return {"success": False,
                    "error": "group name must be at least 2 characters"}
        group = self.get_group(group_id)
        if not group:
            return {"success": False, "error": "group not found"}
        self.db.execute(
            "UPDATE legal_groups SET name = ?, updated_at = datetime('now') "
            "WHERE group_id = ?",
            (name, group["group_id"]),
        )
        self.db.commit()
        self.log_group_audit(group["group_id"], actor, "rename", {"name": name})
        return {"success": True, "group": self.get_group(group["group_id"])}

    def archive_group(self, group_id: str, actor: str = "") -> Dict[str, Any]:
        group = self.get_group(group_id)
        if not group:
            return {"success": False, "error": "group not found"}
        self.db.execute(
            "UPDATE legal_groups SET status = 'archived', "
            "updated_at = datetime('now') WHERE group_id = ?",
            (group["group_id"],),
        )
        self.db.commit()
        self.log_group_audit(group["group_id"], actor, "archive", {})
        return {"success": True, "group": self.get_group(group["group_id"])}

    def list_members(self, group_id: str) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            """SELECT m.*, a.full_name, a.email, a.subscription_tier,
                      a.is_active
               FROM legal_group_members m
               LEFT JOIN juris_accounts a ON a.account_id = m.account_id
               WHERE m.group_id = ?
               ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1
                                    ELSE 2 END, m.created_at""",
            (str(group_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_member(self, group_id: str, account_id: str, role: str = "member",
                   actor: str = "") -> Dict[str, Any]:
        group = self.get_group(group_id)
        if not group:
            return {"success": False, "error": "group not found"}
        if not self.get_account(account_id):
            return {"success": False, "error": "account not found"}
        if role not in ("owner", "admin", "member"):
            return {"success": False, "error": f"invalid role: {role}"}
        cur = self.db.execute(
            """INSERT OR IGNORE INTO legal_group_members
               (group_id, account_id, role, added_by) VALUES (?, ?, ?, ?)""",
            (group["group_id"], account_id, role, str(actor or "")),
        )
        self.db.commit()
        added = cur.rowcount > 0
        if added:
            self.log_group_audit(group["group_id"], actor, "member_add",
                                 {"account_id": account_id, "role": role})
        return {"success": True, "added": added, "group_id": group["group_id"],
                "account_id": account_id, "role": role}

    def remove_member(self, group_id: str, account_id: str,
                      actor: str = "") -> Dict[str, Any]:
        group = self.get_group(group_id)
        if not group:
            return {"success": False, "error": "group not found"}
        cur = self.db.execute(
            "DELETE FROM legal_group_members WHERE group_id = ? AND account_id = ?",
            (group["group_id"], account_id),
        )
        self.db.commit()
        removed = cur.rowcount > 0
        if removed:
            self.log_group_audit(group["group_id"], actor, "member_remove",
                                 {"account_id": account_id})
        return {"success": True, "removed": removed, "account_id": account_id}

    def set_member_role(self, group_id: str, account_id: str, role: str,
                        actor: str = "") -> Dict[str, Any]:
        if role not in ("owner", "admin", "member"):
            return {"success": False, "error": f"invalid role: {role}"}
        cur = self.db.execute(
            "UPDATE legal_group_members SET role = ? "
            "WHERE group_id = ? AND account_id = ?",
            (role, str(group_id), account_id),
        )
        self.db.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "member not found"}
        self.log_group_audit(str(group_id), actor, "member_role",
                             {"account_id": account_id, "role": role})
        return {"success": True, "account_id": account_id, "role": role}

    def bulk_add_members(self, group_id: str, account_ids: List[str],
                         role: str = "member",
                         actor: str = "") -> Dict[str, Any]:
        added = 0
        errors: List[Dict[str, str]] = []
        for aid in account_ids:
            res = self.add_member(group_id, str(aid), role=role, actor=actor)
            if res.get("added"):
                added += 1
            elif not res.get("success"):
                errors.append({"account_id": str(aid),
                               "error": res.get("error", "failed")})
        return {"success": not errors, "added": added, "errors": errors}

    def log_group_audit(self, group_id: str, actor: str, action: str,
                        details: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self.db.execute(
                "INSERT INTO legal_group_audit (group_id, actor, action, details) "
                "VALUES (?, ?, ?, ?)",
                (str(group_id or ""), str(actor or ""), str(action),
                 json.dumps(details or {})),
            )
            self.db.commit()
        except Exception:
            pass  # auditing must never break the action
        return True

    def get_group_audit_log(self, group_id: str,
                            limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM legal_group_audit WHERE group_id = ? "
            "ORDER BY audit_id DESC LIMIT ?",
            (str(group_id), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_group_report(self, group_id: str, *, requested_by: str = "",
                          documents: Optional[List[Dict[str, Any]]] = None,
                          findings: Optional[Dict[str, Any]] = None,
                          summary: str = "", status: str = "completed",
                          verified_count: int = 0,
                          flagged_count: int = 0) -> Dict[str, Any]:
        report_id = "RPT-" + uuid.uuid4().hex[:12].upper()
        docs = documents or []
        self.db.execute(
            """INSERT INTO legal_group_reports
               (report_id, group_id, requested_by, status, document_count,
                verified_count, flagged_count, summary, findings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_id, str(group_id), str(requested_by or ""), status,
             len(docs), int(verified_count), int(flagged_count), summary,
             json.dumps(findings or {})),
        )
        self.db.commit()
        return {"report_id": report_id, "group_id": str(group_id),
                "documents": docs, "summary": summary, "status": status,
                "verified_count": verified_count, "flagged_count": flagged_count,
                "document_count": len(docs)}

    def list_group_reports(self, group_id: str,
                           limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM legal_group_reports WHERE group_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (str(group_id), limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["findings"] = json.loads(d.get("findings") or "{}")
            except (TypeError, ValueError):
                d["findings"] = {}
            out.append(d)
        return out


# Module-level convenience
_account_manager: Optional[AccountManager] = None


def get_account_manager() -> AccountManager:
    """Get or create the singleton AccountManager."""
    global _account_manager
    if _account_manager is None:
        _account_manager = AccountManager()
    return _account_manager
