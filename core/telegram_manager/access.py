"""Telegram Manager — access.json reader/writer.

Reads and writes the live access.json file used by the Telegram MCP server.
All writes are atomic (write to temp file + os.replace) to avoid partial writes
when the MCP server re-reads the file on the next inbound message.

The schema matches the one documented in the Telegram plugin's ACCESS.md.
"""

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from core.telegram_manager.config import ACCESS_JSON_PATH, VALID_DM_POLICIES, VALID_ACK_REACTIONS

logger = logging.getLogger("telegram_manager.access")

_EMPTY_CONFIG: dict[str, Any] = {
    "dmPolicy": "allowlist",
    "allowFrom": [],
    "groups": {},
    "mentionPatterns": [],
    "ackReaction": "",
    "replyToMode": "first",
    "textChunkLimit": 4096,
    "chunkMode": "newline",
}


def read_access_config() -> dict[str, Any]:
    """Read the live access.json file. Returns empty config if missing."""
    if not ACCESS_JSON_PATH.exists():
        logger.warning("access.json not found at %s — returning empty config", ACCESS_JSON_PATH)
        return deepcopy(_EMPTY_CONFIG)

    try:
        raw = ACCESS_JSON_PATH.read_text()
        config = json.loads(raw)
        # Ensure all expected keys exist
        merged = deepcopy(_EMPTY_CONFIG)
        merged.update(config)
        return merged
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read access.json: %s", exc)
        return deepcopy(_EMPTY_CONFIG)


def write_access_config(config: dict[str, Any]) -> bool:
    """Atomically write the full access config to access.json.

    Uses temp file + os.replace for atomicity — the MCP server re-reads
    access.json on every inbound message, so partial writes would be visible.
    """
    ACCESS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        tmp = NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=str(ACCESS_JSON_PATH.parent),
            delete=False,
        )
        json.dump(config, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(ACCESS_JSON_PATH))
        logger.info("access.json written — %d allowFrom, dmPolicy=%s", len(config.get("allowFrom", [])), config.get("dmPolicy", "?"))
        return True
    except OSError as exc:
        logger.error("Failed to write access.json: %s", exc)
        return False


# ── User management ────────────────────────────────────────────────────────

def add_user(user_id: str) -> dict[str, Any]:
    """Add a user ID to the allowlist. Idempotent — skips duplicates."""
    config = read_access_config()
    allow_from = config.get("allowFrom", [])
    if user_id in allow_from:
        return {"success": True, "user_id": user_id, "already_allowed": True, "config": config}

    allow_from.append(user_id)
    config["allowFrom"] = allow_from
    written = write_access_config(config)
    return {"success": written, "user_id": user_id, "already_allowed": False, "config": config if written else None}


def remove_user(user_id: str) -> dict[str, Any]:
    """Remove a user ID from the allowlist."""
    config = read_access_config()
    allow_from = config.get("allowFrom", [])
    if user_id not in allow_from:
        return {"success": True, "user_id": user_id, "was_allowed": False, "config": config}

    config["allowFrom"] = [uid for uid in allow_from if uid != user_id]
    written = write_access_config(config)
    return {"success": written, "user_id": user_id, "was_allowed": True, "config": config if written else None}


# ── Policy management ──────────────────────────────────────────────────────

def set_policy(policy: str) -> dict[str, Any]:
    """Set the DM policy (pairing / allowlist / disabled)."""
    if policy not in VALID_DM_POLICIES:
        return {"success": False, "error": f"Invalid policy '{policy}'. Valid: {', '.join(VALID_DM_POLICIES)}"}

    config = read_access_config()
    old = config.get("dmPolicy")
    config["dmPolicy"] = policy
    written = write_access_config(config)
    return {"success": written, "policy": policy, "previous": old, "config": config if written else None}


def set_ack_reaction(emoji: str) -> dict[str, Any]:
    """Set the acknowledgement reaction emoji. Empty string disables."""
    if emoji and emoji not in VALID_ACK_REACTIONS:
        return {"success": False, "error": f"Invalid reaction emoji '{emoji}'. Must be from Telegram's fixed whitelist."}

    config = read_access_config()
    old = config.get("ackReaction", "")
    config["ackReaction"] = emoji
    written = write_access_config(config)
    return {"success": written, "ackReaction": emoji, "previous": old, "config": config if written else None}


def set_reply_mode(mode: str) -> dict[str, Any]:
    """Set the reply-to mode (first / all / off)."""
    from core.telegram_manager.config import VALID_REPLY_MODES
    if mode not in VALID_REPLY_MODES:
        return {"success": False, "error": f"Invalid reply mode '{mode}'. Valid: {', '.join(VALID_REPLY_MODES)}"}

    config = read_access_config()
    old = config.get("replyToMode", "first")
    config["replyToMode"] = mode
    written = write_access_config(config)
    return {"success": written, "replyToMode": mode, "previous": old, "config": config if written else None}


# ── Group management ───────────────────────────────────────────────────────

def add_group(group_id: str, require_mention: bool = True, allow_from: list[str] | None = None) -> dict[str, Any]:
    """Add a group to the config."""
    config = read_access_config()
    groups = config.get("groups", {})

    existing = groups.get(group_id, {})
    groups[group_id] = {
        "requireMention": require_mention,
        "allowFrom": allow_from or existing.get("allowFrom", []),
    }

    config["groups"] = groups
    written = write_access_config(config)
    return {"success": written, "group_id": group_id, "config": config if written else None}


def remove_group(group_id: str) -> dict[str, Any]:
    """Remove a group from the config."""
    config = read_access_config()
    groups = config.get("groups", {})
    if group_id not in groups:
        return {"success": True, "group_id": group_id, "was_present": False, "config": config}

    del groups[group_id]
    config["groups"] = groups
    written = write_access_config(config)
    return {"success": written, "group_id": group_id, "was_present": True, "config": config if written else None}
