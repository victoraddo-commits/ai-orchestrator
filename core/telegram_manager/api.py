"""Telegram Manager — FastAPI router for the Telegram management dashboard.

Provides REST endpoints for user management, activity tracking, and
access control configuration. Read endpoints are open; mutation endpoints
require write capability (auth gate applied in core/api.py).
"""

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from core.telegram_manager.access import (
    read_access_config,
    add_user,
    remove_user,
    set_policy,
    set_ack_reaction,
    set_reply_mode,
    add_group,
    remove_group,
)
from core.telegram_manager.db import (
    log_message,
    upsert_user_profile,
    get_summary,
    get_users,
    get_user_activity,
    get_recent_activity,
    get_daily_stats,
    clear_old_logs,
)

logger = logging.getLogger("telegram_manager.api")

telegram_router = APIRouter(prefix="/api/telegram", tags=["telegram"])


# ── Dashboard stats ────────────────────────────────────────────────────────

@telegram_router.get("/stats")
def telegram_stats():
    """Dashboard summary: user counts, message activity, access policy."""
    try:
        db_summary = get_summary()
        access = read_access_config()
        return {
            **db_summary,
            "dm_policy": access.get("dmPolicy", "unknown"),
            "allowed_users": len(access.get("allowFrom", [])),
            "groups_enabled": len(access.get("groups", {})),
            "ack_reaction": access.get("ackReaction", ""),
            "reply_to_mode": access.get("replyToMode", "first"),
        }
    except Exception as e:
        logger.error("stats error: %s", e)
        return {"error": str(e)}


# ── Users ──────────────────────────────────────────────────────────────────

@telegram_router.get("/users")
def telegram_users(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
                   search: str = Query("")):
    """List known users with activity stats and allowlist status."""
    try:
        return get_users(page=page, per_page=per_page, search=search)
    except Exception as e:
        return {"error": str(e), "users": [], "total": 0}


@telegram_router.get("/users/{user_id}")
def telegram_user_detail(user_id: str):
    """Get a single user's profile and recent activity."""
    try:
        activity = get_user_activity(user_id, limit=50)
        from core.telegram_manager.access import read_access_config
        access = read_access_config()
        is_allowed = user_id in access.get("allowFrom", [])

        # Get profile
        import sqlite3
        from core.telegram_manager.config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        profile = conn.execute(
            "SELECT * FROM telegram_user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()

        return {
            "user_id": user_id,
            "profile": dict(profile) if profile else None,
            "is_allowed": is_allowed,
            "recent_activity": activity,
        }
    except Exception as e:
        return {"error": str(e), "user_id": user_id}


# ── User admin actions ─────────────────────────────────────────────────────

@telegram_router.post("/users/{user_id}/allow")
def telegram_allow_user(user_id: str, body: dict = Body(default={})):
    """Add a user to the Telegram allowlist."""
    try:
        result = add_user(user_id)
        if result.get("success") and not result.get("already_allowed"):
            # Optionally update profile
            name = body.get("display_name", "")
            notes = body.get("notes", "")
            if name or notes:
                upsert_user_profile(user_id, display_name=name, notes=notes)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.post("/users/{user_id}/remove")
def telegram_remove_user(user_id: str):
    """Remove a user from the Telegram allowlist."""
    try:
        return remove_user(user_id)
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.put("/users/{user_id}/profile")
def telegram_update_profile(user_id: str, body: dict = Body(...)):
    """Update a user's display name or notes."""
    try:
        ok = upsert_user_profile(
            user_id,
            display_name=body.get("display_name", ""),
            notes=body.get("notes", ""),
        )
        return {"success": ok, "user_id": user_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Config ─────────────────────────────────────────────────────────────────

@telegram_router.get("/config")
def telegram_config():
    """Get the full Telegram access configuration."""
    try:
        return {"config": read_access_config()}
    except Exception as e:
        return {"error": str(e), "config": None}


@telegram_router.put("/config/policy")
def telegram_set_policy(body: dict = Body(...)):
    """Set the DM policy (pairing / allowlist / disabled)."""
    try:
        return set_policy(body.get("policy", ""))
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.put("/config/ack-reaction")
def telegram_set_ack_reaction(body: dict = Body(...)):
    """Set the acknowledgement reaction emoji."""
    try:
        return set_ack_reaction(body.get("emoji", ""))
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.put("/config/reply-mode")
def telegram_set_reply_mode(body: dict = Body(...)):
    """Set the reply-to mode (first / all / off)."""
    try:
        return set_reply_mode(body.get("mode", "first"))
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.post("/config/groups")
def telegram_manage_group(body: dict = Body(...)):
    """Add or remove a group. Use action: 'add' or 'remove'."""
    try:
        action = body.get("action", "add")
        group_id = body.get("group_id", "")
        if not group_id:
            return {"success": False, "error": "group_id is required"}

        if action == "remove":
            return remove_group(group_id)
        else:
            return add_group(
                group_id,
                require_mention=body.get("require_mention", True),
                allow_from=body.get("allow_from", []),
            )
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Activity ───────────────────────────────────────────────────────────────

@telegram_router.get("/activity")
def telegram_activity(limit: int = Query(100, ge=1, le=500)):
    """Get recent message activity."""
    try:
        return {"activity": get_recent_activity(limit=limit)}
    except Exception as e:
        return {"error": str(e), "activity": []}


@telegram_router.post("/activity/log")
def telegram_log_activity(body: dict = Body(...)):
    """Log a Telegram message to the activity database.

    Called by the assistant or a hook after processing a message.
    Body: {chat_id, from_user_id, direction, content_preview, content_length, chat_type}
    """
    try:
        log_id = log_message(
            chat_id=str(body.get("chat_id", "")),
            from_user_id=str(body.get("from_user_id", "")),
            direction=body.get("direction", "in"),
            content_preview=body.get("content_preview", ""),
            content_length=body.get("content_length", 0),
            chat_type=body.get("chat_type", "dm"),
        )
        return {"success": log_id > 0, "log_id": log_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


@telegram_router.get("/activity/stats")
def telegram_activity_stats(days: int = Query(14, ge=1, le=90)):
    """Get daily activity stats."""
    try:
        return {"daily_stats": get_daily_stats(days=days)}
    except Exception as e:
        return {"error": str(e), "daily_stats": []}


# ── Ops ───────────────────────────────────────────────────────────────────

@telegram_router.post("/ops/clear-logs")
def telegram_clear_logs(body: dict = Body(default={})):
    """Clear old activity logs."""
    try:
        retention = body.get("retention_days", 90)
        deleted = clear_old_logs(retention_days=retention)
        return {"success": True, "deleted": deleted}
    except Exception as e:
        return {"success": False, "error": str(e)}
