"""KAI Media Revenue Factory — configuration, feature flags and status vocabulary.

Nothing in this module performs I/O. Feature flags are *functions* so the
environment can be changed at runtime (and monkeypatched in tests) without a
module reload. All paths / endpoints / credentials are env-overridable and
default to the values verified on CT111 on 2026-09-18.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
MIGRATIONS_DIR = PACKAGE_DIR / "migrations"

# Generated media never lands in the repo; CT111 keeps it under /var/lib.
MEDIA_DATA_DIR = Path(os.environ.get("MEDIA_DATA_DIR", "/var/lib/ai-orchestrator/media"))

# ── Postgres (existing klaus_db cluster, 127.0.0.1:5432) ───────────────────
DB_HOST = os.environ.get("MEDIA_DB_HOST", os.environ.get("KLAUS_DB_HOST", "127.0.0.1"))
DB_PORT = int(os.environ.get("MEDIA_DB_PORT", os.environ.get("KLAUS_DB_PORT", "5432")))
DB_NAME = os.environ.get("MEDIA_DB_NAME", "kai_media")
DB_USER = os.environ.get("MEDIA_DB_USER", os.environ.get("KLAUS_DB_USER", "klaus_user"))
# Password: env wins, else vault, else the documented klaus local fallback.
DB_PASSWORD_ENV = os.environ.get("MEDIA_DB_PASSWORD", os.environ.get("KLAUS_DB_PASSWORD", ""))
DB_PASSWORD_FALLBACK = "klaus_password"
DB_MAINTENANCE_NAME = os.environ.get("MEDIA_DB_MAINTENANCE", "postgres")

# Vault path for the kai_media role (reuses core.ai.kai_vault_client).
VAULT_SECRET_PATH = os.environ.get(
    "MEDIA_VAULT_SECRET_PATH", "secrets/infrastructure/kai-media-db"
)


def db_config(database: str | None = None) -> dict:
    """psycopg2 kwargs for the media database (or a maintenance db)."""
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": database or DB_NAME,
        "user": DB_USER,
        "connect_timeout": int(os.environ.get("MEDIA_DB_CONNECT_TIMEOUT", "5")),
    }


# ── Feature flags ──────────────────────────────────────────────────────────
def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def media_enabled() -> bool:
    """Master switch — when false, no cycle will run."""
    return _flag("MEDIA_ENABLED", True)


def dry_run() -> bool:
    """When true (default) publishing never touches a platform."""
    return _flag("MEDIA_DRY_RUN", True)


def publish_enabled() -> bool:
    """Explicit gate for real publishing; default OFF."""
    return _flag("MEDIA_PUBLISH_ENABLED", False)


def media_cycle_min_interval() -> float:
    return float(os.environ.get("MEDIA_CYCLE_MIN_INTERVAL", "900"))


def auth_required() -> bool:
    """When true, media write endpoints require a bridge/session capability."""
    return _flag("MEDIA_AUTH_REQUIRED", False)


# ── Local model fabric (Ollama, OpenAI-compatible surface) ─────────────────
LLM_BASE_URL = os.environ.get("MEDIA_LLM_BASE_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("MEDIA_LLM_MODEL", "qwen3-coder:kai")
LLM_TIMEOUT = float(os.environ.get("MEDIA_LLM_TIMEOUT", "120"))

# ── Trend discovery ────────────────────────────────────────────────────────
TREND_GEO = os.environ.get("MEDIA_TREND_GEO", "GH")
GOOGLE_TRENDS_RSS = os.environ.get(
    "MEDIA_TREND_SOURCE_URL", "https://trends.google.com/trending/rss?geo=GH"
)
TREND_HTTP_TIMEOUT = float(os.environ.get("MEDIA_TREND_TIMEOUT", "12"))

# ── Status vocabulary (§1.14) ──────────────────────────────────────────────
STATUS_VERIFIED = "VERIFIED"
STATUS_PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_MISSING = "MISSING"
STATUS_BLOCKED = "BLOCKED"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

STATUS_VALUES = (
    STATUS_VERIFIED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_UNVERIFIED,
    STATUS_MISSING,
    STATUS_BLOCKED,
    STATUS_DEGRADED,
    STATUS_FAILED,
)

# ── Honest capability reasons (single source of truth for /status) ─────────
BLOCKED_ASSET_GEN = "no image/video models installed; no ffmpeg"
BLOCKED_VOICE = "no TTS/voice model installed"
BLOCKED_EDITING = "no ffmpeg installed"
BLOCKED_CAPTIONS = "no caption/ASR tooling installed"
BLOCKED_PUBLISHING = "no TikTok/YouTube/platform OAuth tokens configured"
BLOCKED_ANALYTICS = "no platform API tokens configured"
