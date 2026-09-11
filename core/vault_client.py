"""Vault integration client with .env fallback.

Tries to read secrets from the KAI Vault service. When vault is
unreachable or the key isn't stored there, falls back to os.environ.
This lets the codebase migrate to vault incrementally — secrets work
from .env today and automatically switch when vault is online.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

VAULT_URLS = [
    os.environ.get("VAULT_URL", "https://vault.sso.deerude.com"),
    "https://vault.local",
]

_CAPABILITIES_PATH = Path(__file__).parent.parent / "config" / "vault_capabilities.json"

_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300


def _load_capabilities() -> dict:
    try:
        return json.loads(_CAPABILITIES_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _vault_request(path: str, timeout: float = 5.0) -> Optional[str]:
    """Try each vault URL, return the secret value or None."""
    for base_url in VAULT_URLS:
        url = f"{base_url}/v1/{path}"
        try:
            req = Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                value = data.get("data", {}).get("value")
                if value:
                    return value
        except (URLError, OSError, json.JSONDecodeError, KeyError):
            continue
    return None


def get_secret(vault_path: str, env_var: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret, trying vault first then .env fallback.

    Args:
        vault_path: Vault KV path (e.g. "secrets/ai-providers/gemini")
        env_var: Environment variable name to fall back to
        default: Default if neither source has the value
    """
    now = time.monotonic()
    cached = _cache.get(vault_path)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    value = _vault_request(vault_path)
    if value:
        _cache[vault_path] = (value, now)
        return value

    env_value = os.environ.get(env_var)
    if env_value:
        return env_value

    return default


def is_vault_available() -> bool:
    """Check if any vault endpoint responds."""
    for base_url in VAULT_URLS:
        try:
            req = Request(f"{base_url}/health", method="GET")
            with urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except (URLError, OSError):
            continue
    return False


def clear_cache() -> None:
    """Clear the in-memory secret cache."""
    _cache.clear()


SECRET_MAP = {
    "secrets/ai-providers/gemini": "GEMINI_API_KEY",
    "secrets/ai-providers/geminix": "GEMINIX_API_KEY",
    "secrets/ai-providers/groq": "GROQ_API_KEY",
    "secrets/ai-providers/openai": "OPENAI_API_KEY",
    "secrets/ai-providers/openrouter": "OPENROUTER_API_KEY",
    "secrets/ai-providers/minimax": "MINIMAX_API_KEY",
    "secrets/ai-providers/deepseek-openrouter": "DEEPSEEK_OPENROUTER_API_KEY",
    "secrets/ai-providers/deepseek-native-pro": "DEEPSEEK_NATIVE_PRO_API_KEY",
    "secrets/ai-providers/deepseek-native-flash": "DEEPSEEK_NATIVE_FLASH_API_KEY",
    "secrets/ai-providers/claude-fable5": "CLAUDE_FABLE5_OPENCODE_ZEN_API_KEY",
    "secrets/ai-providers/gemini-3-1-pro": "GEMINI_3_1_PRO_OPENCODE_ZEN_API_KEY",
    "secrets/telegram/kai-enzo-bot": "KAI_TELEGRAM_BOT_TOKEN",
    "secrets/telegram/susu-bot": "SUSU_BOT_TOKEN",
    "secrets/telegram/law-tutor-bot": "LAW_TUTOR_BOT_TOKEN",
    "secrets/telegram/juris-kai-bot": "JURIS_KAI_BOT_TOKEN",
    "secrets/infrastructure/proxmox-a": "PROXMOX_TOKEN",
    "secrets/infrastructure/proxmox-b": "PROXMOX_B_TOKEN_SECRET",
    "secrets/infrastructure/opnsense": "OPNSENSE_API_KEY",
    "secrets/infrastructure/redis": "REDIS_PASSWORD",
    "secrets/infrastructure/proxdash": "PROXDASH_APP_PASSWORD",
    "secrets/infrastructure/ddwrt": "DDWRT_PASSWORD",
    "secrets/external/odds-api-io": "ODDS_API_IO_KEY",
}


def get_by_path(vault_path: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret by vault path, auto-resolving the .env fallback."""
    env_var = SECRET_MAP.get(vault_path)
    if not env_var:
        return _vault_request(vault_path) or default
    return get_secret(vault_path, env_var, default)
