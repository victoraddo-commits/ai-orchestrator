"""KAI Telegram Module — Bot Registry (directive §20-§22).

One shared registry mapping every Telegram bot to its *functional owner*
(the Kai application/module that owns its business logic), its capabilities,
and its enabled state. This is the deterministic routing source of truth:

    bot identity → owning module → capabilities → permission check

Design rules (directive §1, §3, §20, §22):
  * Ownership is explicit. Unknown bots are denied — never guessed from
    message wording, username heuristics, or AI inference.
  * A bot's ownership never implies access to another module's capabilities
    (capability isolation, deny-by-default).
  * Adding a bot = register it here + assign an owner + capabilities.

This module is stdlib-only and import-safe on the runner (CT 111).
"""
from __future__ import annotations

from dataclasses import dataclass, field

DENY = "deny"  # default security posture


@dataclass(frozen=True)
class Bot:
    bot_id: str
    username: str
    token_env: str
    owner_application: str
    owner_module: str
    capabilities: frozenset = field(default_factory=frozenset)
    mode: str = "polling"
    enabled: bool = True
    host: str = ""
    env_file: str = ""
    status: str = "active"  # active | dormant | retired


# --- Registry (source of truth) -------------------------------------------
# Populated from the Phase-1 discovery audit (2026-09-13), corrected with the
# operator-supplied ownership: betsportz_bot + susugh_bot are owned by the
# bet/susu modules on LXC 103 ("bet-susu"). Do not invent values.
BOTS: dict[str, Bot] = {
    "kai-enzo-bot": Bot(
        bot_id="kai-enzo-bot",
        username="KaiEnzo_bot",
        token_env="KAI_TELEGRAM_BOT_TOKEN",
        owner_application="Kai Core (ai-orchestrator)",
        owner_module="kai_core",
        capabilities=frozenset({
            "kai.notify", "kai.approval", "kai.build", "kai.money",
            "kai.status", "kai.roadmap",
        }),
        host="ct111", env_file="/opt/ai-orchestrator/.env",
        enabled=True,
    ),
    "juris-kai-bot": Bot(
        bot_id="juris-kai-bot",
        username="Juriskai_bot",
        token_env="JURIS_KAI_BOT_TOKEN",
        owner_application="Juris Kai",
        owner_module="juris_kai",
        capabilities=frozenset({
            "legal.query", "legal.retrieve", "legal.cite", "legal.memory",
        }),
        host="ct111", env_file="/opt/ai-orchestrator/.env",
        enabled=True,
    ),
    "betsportz-bot": Bot(
        bot_id="betsportz-bot",
        username="betsportz_bot",
        token_env="BETSPORTZ_BOT_TOKEN",
        owner_application="Kai Betting",
        owner_module="kai_betting",
        capabilities=frozenset({"betting.query", "betting.tips"}),
        host="ct111", env_file="/etc/kai/kai_betting.env",
        enabled=True,
    ),
    "susugh-bot": Bot(
        bot_id="susugh-bot",
        username="susugh_bot",
        token_env="TELEGRAM_BOT_TOKEN",
        owner_application="SUSU (savings groups)",
        owner_module="susu",
        capabilities=frozenset({"susu.group", "susu.payment", "susu.account"}),
        host="ct103", env_file="/opt/susu/.env",
        enabled=True,
    ),
    "deerudeclaude-bot": Bot(
        bot_id="deerudeclaude-bot",
        username="DeerudeClaude_Bot",
        token_env="",           # plugin-managed (Claude Code plugin); unverified
        owner_application="OpenCode / Claude Code (agent updates)",
        owner_module="agent_notify",
        capabilities=frozenset({"notify.send"}),
        host="ct113", env_file="",
        enabled=True,
    ),
    "akush233-bot": Bot(
        bot_id="akush233-bot",
        username="akush233bot",
        token_env="TELEGRAM_BOT_TOKEN",
        owner_application="Kai Money (crypto + money)",
        owner_module="kai_money",
        capabilities=frozenset({"money.notify", "crypto.notify"}),
        host="ct108",
        env_file="/opt/kai-money/secrets/telegram_bot_token.txt",
        enabled=True,
    ),
}

# Explicit denylist (directive §20/§45). A bot listed here is NOT owned by this
# system and must never be routed or re-registered, even if a token reappears.
# `@vadomfeh_bot` (ex "law tutor") is not operator-owned — blocked + removed
# 2026-09-13; its credential is stripped from CT 111 config.
BLOCKED_BOT_NAMES = frozenset({
    "vadomfeh_bot", "law_tutor_bot", "law-tutor-bot",
})


def is_blocked(name: str) -> bool:
    return (name or "").lstrip("@").lower() in BLOCKED_BOT_NAMES

# Capabilities that are only ever granted by explicit, audited registration.
# Used by the guard to hard-deny privilege-escalation probes.
PRIVILEGED_CAPABILITIES = frozenset({
    "proxmox.admin", "ssh.execute", "vault.read", "filesystem.write",
    "shell.execute", "telegram.admin", "kai.core.admin", "secrets.read",
})


def all_bots() -> list:
    return list(BOTS.values())


def get(bot_id: str):
    return BOTS.get(bot_id or "")


def by_username(username: str):
    uname = (username or "").lstrip("@").lower()
    if is_blocked(uname):
        return None
    for bot in BOTS.values():
        if bot.username.lower() == uname:
            return bot
    return None


def by_token_env(token_env: str):
    """Resolve by token env var — only when unambiguous.

    Two bots may share an env var *name* in different env files (e.g. the bet
    and susu bots both use TELEGRAM_BOT_TOKEN). An ambiguous name resolves to
    None (deny) rather than guessing.
    """
    matches = [b for b in BOTS.values() if b.token_env == token_env]
    return matches[0] if len(matches) == 1 else None


def by_host_env(host: str, env_file: str, token_env: str):
    for bot in BOTS.values():
        if (bot.host == host and bot.env_file == env_file
                and bot.token_env == token_env):
            return bot
    return None


def resolve_bot(bot_id: str = None, username: str = None,
                token_env: str = None, host: str = None,
                env_file: str = None):
    """Deterministic bot resolution. Returns the Bot or None (unknown).

    Resolution uses registered identity only — never message content.
    """
    if bot_id:
        return get(bot_id)
    if username:
        return by_username(username)
    if token_env:
        if host or env_file:
            return by_host_env(host or "", env_file or "", token_env)
        return by_token_env(token_env)
    return None


def is_enabled(bot) -> bool:
    return bool(bot and bot.enabled and getattr(bot, "status", "active") != "retired")


def is_retired(bot) -> bool:
    return bool(bot and getattr(bot, "status", "active") == "retired")


def has_capability(bot, capability: str) -> bool:
    """Deny-by-default capability check. Exact-match only — no wildcards.

    A disabled or unknown bot has no capabilities.
    """
    if not is_enabled(bot):
        return False
    return capability in bot.capabilities


def authorize(bot, capability: str) -> dict:
    """Return an authorization decision for a (bot, capability) pair."""
    if bot is None:
        return {"allowed": False, "reason": "unknown bot", "decided": DENY}
    if is_retired(bot):
        return {"allowed": False, "reason": "bot retired", "decided": DENY}
    if not bot.enabled:
        return {"allowed": False, "reason": "bot disabled", "decided": DENY}
    if capability in PRIVILEGED_CAPABILITIES:
        return {"allowed": False,
                "reason": "privileged capability requires explicit grant",
                "decided": DENY}
    if capability not in bot.capabilities:
        return {"allowed": False,
                "reason": f"capability {capability!r} not granted to "
                          f"{bot.owner_module}", "decided": DENY}
    return {"allowed": True, "reason": "granted", "decided": "allow"}
