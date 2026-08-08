"""Telegram Manager — configuration constants and paths."""

import os
from pathlib import Path

# access.json is the MCP server's source of truth — live config, not a copy
ACCESS_JSON_PATH = Path(os.path.expanduser("~/.claude/channels/telegram/access.json"))

# Activity database lives alongside other Kai memory files
DB_DIR = os.environ.get("TELEGRAM_MANAGER_DB_DIR",
    str(Path(__file__).parent.parent.parent / "memory"))
DB_PATH = os.path.join(DB_DIR, "telegram_manager.db")

# Valid DM policy values (from the MCP server's ACCESS.md)
VALID_DM_POLICIES = ("pairing", "allowlist", "disabled")

# Valid reply-to modes
VALID_REPLY_MODES = ("first", "all", "off")

# Valid chunk modes
VALID_CHUNK_MODES = ("length", "newline")

# Telegram's fixed emoji whitelist for reactions
VALID_ACK_REACTIONS = frozenset({
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
    "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨",
    "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿",
    "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂",
    "🤷", "🤷‍♀", "😡",
})

# Max text chunk size (Telegram limit is 4096, we cap slightly lower for safety)
MAX_TEXT_CHUNK = 4096
