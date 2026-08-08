"""Telegram Manager — SQLite activity tracking database.

Tracks message activity and user profiles for the Telegram management dashboard.
This is NOT a replacement for Telegram's native state — it's a local audit log
that the dashboard reads to show activity history.

Telegram's Bot API provides no message history, so this DB is populated via a
POST endpoint. The assistant (or a hook) calls it after processing messages.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.telegram_manager.config import DB_PATH

logger = logging.getLogger("telegram_manager.db")


def _get_connection() -> sqlite3.Connection:
    """Get a connection to the activity database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Ensure tables exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS telegram_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            from_user_id TEXT,
            direction TEXT NOT NULL CHECK(direction IN ('in','out')),
            content_preview TEXT,
            content_length INTEGER DEFAULT 0,
            chat_type TEXT DEFAULT 'dm',
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS telegram_user_profiles (
            user_id TEXT PRIMARY KEY,
            display_name TEXT DEFAULT '',
            first_seen TEXT,
            last_seen TEXT,
            message_count_in INTEGER DEFAULT 0,
            message_count_out INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS telegram_daily_stats (
            date TEXT NOT NULL,
            user_id TEXT NOT NULL,
            inbound_count INTEGER DEFAULT 0,
            outbound_count INTEGER DEFAULT 0,
            PRIMARY KEY (date, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_activity_chat_id ON telegram_activity_log(chat_id);
        CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON telegram_activity_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_activity_user_id ON telegram_activity_log(from_user_id);
        CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON telegram_daily_stats(date);
    """)


# ── Activity logging ───────────────────────────────────────────────────────

def log_message(chat_id: str, from_user_id: str, direction: str,
                content_preview: str = "", content_length: int = 0,
                chat_type: str = "dm") -> int:
    """Log an inbound or outbound Telegram message.

    Also updates the user profile and daily stats counters.
    Returns the new activity log row ID.
    """
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    uid = str(from_user_id)

    try:
        cur = conn.execute(
            """INSERT INTO telegram_activity_log
               (chat_id, from_user_id, direction, content_preview, content_length, chat_type, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(chat_id), uid, direction, content_preview[:200] if content_preview else "",
             content_length, chat_type, now),
        )
        log_id = cur.lastrowid

        # Upsert user profile
        existing = conn.execute(
            "SELECT user_id FROM telegram_user_profiles WHERE user_id = ?", (uid,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE telegram_user_profiles
                   SET last_seen = ?,
                       message_count_in = message_count_in + ?,
                       message_count_out = message_count_out + ?
                   WHERE user_id = ?""",
                (now, 1 if direction == "in" else 0, 1 if direction == "out" else 0, uid),
            )
        else:
            conn.execute(
                """INSERT INTO telegram_user_profiles
                   (user_id, display_name, first_seen, last_seen,
                    message_count_in, message_count_out)
                   VALUES (?, '', ?, ?, ?, ?)""",
                (uid, now, now, 1 if direction == "in" else 0, 1 if direction == "out" else 0),
            )

        # Upsert daily stats
        conn.execute(
            """INSERT INTO telegram_daily_stats (date, user_id, inbound_count, outbound_count)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date, user_id) DO UPDATE SET
               inbound_count = inbound_count + excluded.inbound_count,
               outbound_count = outbound_count + excluded.outbound_count""",
            (today, uid, 1 if direction == "in" else 0, 1 if direction == "out" else 0),
        )

        conn.commit()
        return log_id
    except Exception as exc:
        logger.error("Failed to log message: %s", exc)
        return -1
    finally:
        conn.close()


def upsert_user_profile(user_id: str, display_name: str = "",
                        notes: str = "") -> bool:
    """Create or update a user profile."""
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()
    uid = str(user_id)
    try:
        existing = conn.execute(
            "SELECT user_id FROM telegram_user_profiles WHERE user_id = ?", (uid,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE telegram_user_profiles
                   SET display_name = CASE WHEN ? != '' THEN ? ELSE display_name END,
                       notes = CASE WHEN ? != '' THEN ? ELSE notes END
                   WHERE user_id = ?""",
                (display_name, display_name, notes, notes, uid),
            )
        else:
            conn.execute(
                """INSERT INTO telegram_user_profiles
                   (user_id, display_name, first_seen, last_seen, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (uid, display_name, now, now, notes),
            )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("Failed to upsert user: %s", exc)
        return False
    finally:
        conn.close()


# ── Query methods ──────────────────────────────────────────────────────────

def get_summary() -> dict[str, Any]:
    """Dashboard summary: total users, messages today, active chats, policy status."""
    conn = _get_connection()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        total_users = conn.execute("SELECT COUNT(*) as c FROM telegram_user_profiles").fetchone()["c"]
        msgs_today = conn.execute(
            "SELECT direction, COUNT(*) as c FROM telegram_activity_log WHERE date(timestamp) = ? GROUP BY direction",
            (today,),
        ).fetchall()
        active_chats = conn.execute(
            "SELECT COUNT(DISTINCT chat_id) as c FROM telegram_activity_log WHERE date(timestamp) = ?",
            (today,),
        ).fetchone()["c"]

        msgs_in = sum(r["c"] for r in msgs_today if r["direction"] == "in")
        msgs_out = sum(r["c"] for r in msgs_today if r["direction"] == "out")

        return {
            "total_users": total_users,
            "messages_today": msgs_in + msgs_out,
            "messages_in_today": msgs_in,
            "messages_out_today": msgs_out,
            "active_chats_today": active_chats,
        }
    finally:
        conn.close()


def get_users(page: int = 1, per_page: int = 50,
              search: str = "") -> dict[str, Any]:
    """Paginated list of users with activity stats, enriched with allowlist status."""
    from core.telegram_manager.access import read_access_config
    access = read_access_config()
    allowed_ids = set(access.get("allowFrom", []))

    conn = _get_connection()
    try:
        where = ""
        params = []
        if search:
            where = "WHERE (user_id LIKE ? OR display_name LIKE ?)"
            q = f"%{search}%"
            params = [q, q]

        total = conn.execute(
            f"SELECT COUNT(*) as c FROM telegram_user_profiles {where}", params,
        ).fetchone()["c"]

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT * FROM telegram_user_profiles {where}
                ORDER BY last_seen DESC LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        ).fetchall()

        users = []
        for r in rows:
            d = dict(r)
            d["is_allowed"] = d["user_id"] in allowed_ids
            users.append(d)

        return {
            "users": users,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        conn.close()


def get_user_activity(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent messages for a single user."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM telegram_activity_log
               WHERE from_user_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (str(user_id), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_activity(limit: int = 100) -> list[dict[str, Any]]:
    """Get the most recent activity across all users."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM telegram_activity_log
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_daily_stats(days: int = 14) -> list[dict[str, Any]]:
    """Aggregate daily inbound/outbound counts."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT date,
                      SUM(inbound_count) as inbound,
                      SUM(outbound_count) as outbound
               FROM telegram_daily_stats
               WHERE date >= date('now', ?)
               GROUP BY date
               ORDER BY date ASC""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_old_logs(retention_days: int = 90) -> int:
    """Delete activity logs older than the retention period. Returns count deleted."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM telegram_activity_log WHERE timestamp < datetime('now', ?)",
            (f"-{retention_days} days",),
        )
        conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("Cleared %d old telegram activity log entries", deleted)
        return deleted
    finally:
        conn.close()
