"""POST /notify — ecosystem alert endpoint (merged from kai-notify)."""
import os
import time
import hashlib
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from core.telegram_bridge import send_telegram_alert

router = APIRouter(prefix="/notify", tags=["notifications"])

# Authorized bearer tokens for ecosystem services
# Tokens are stored in environment as comma-separated list OR in a file (one per line)
# This allows tokens to be updated without code changes
_AUTHORIZED_TOKENS: set[str] = set()

def _load_tokens() -> set[str]:
    tokens: set[str] = set()
    # Load from environment variable (comma-separated)
    env_val = os.environ.get("KAI_NOTIFY_AUTHORIZED_TOKENS", "")
    if env_val:
        tokens.update(t.strip() for t in env_val.split(",") if t.strip())
    # Load from token file (one per line, path configured via env)
    token_file = os.environ.get("KAI_NOTIFY_TOKENS_FILE", "")
    if token_file:
        try:
            with open(token_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        tokens.add(line)
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return tokens

_AUTHORIZED_TOKENS = _load_tokens()

# Deduplication window in seconds (matches kai-notify 10min window)
DEDUP_WINDOW_SECS = 600
_rate_limit_store: dict[str, float] = {}  # source → last_sent timestamp
_dedup_store: dict[str, float] = {}  # key → last_sent timestamp

class NotifyPayload(BaseModel):
    source: str
    severity: str  # "info" | "warn" | "critical"
    title: str
    message: str
    chat_id: Optional[str] = None

def _source_key(source: str, title: str) -> str:
    """Deduplication key — source + title within the dedup window."""
    return hashlib.sha256(f"{source}:{title}".encode()).hexdigest()[:16]

def _check_rate_limit(source: str) -> bool:
    """True if under the 20/min per-source rate limit."""
    now = time.time()
    window = [t for t in _rate_limit_store.values() if now - t < 60]
    _rate_limit_store[source] = now
    return len(window) < 20

@router.post("")
async def notify_event(
    payload: NotifyPayload,
    authorization: str = Header(None),
):
    # Bearer token authentication
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization.removeprefix("Bearer ").strip()
    if not _AUTHORIZED_TOKENS:
        # No tokens configured — allow all (open mode for initial deployment)
        pass
    elif token not in _AUTHORIZED_TOKENS:
        raise HTTPException(status_code=403, detail="Invalid token")

    # Rate limit per source
    if not _check_rate_limit(payload.source):
        raise HTTPException(status_code=429, detail="Rate limited")

    # Deduplication
    key = _source_key(payload.source, payload.title)
    now = time.time()
    last_sent = _dedup_store.get(key, 0)
    if now - last_sent < DEDUP_WINDOW_SECS:
        return {"status": "deduped", "key": key}

    # Store dedup marker
    _dedup_store[key] = now

    # Severity routing — warn/critical go to Telegram; info stored only
    if payload.severity in ("warn", "critical"):
        emoji = "⚠️" if payload.severity == "warn" else "🚨"
        text = f"{emoji} [{payload.source}] {payload.title}\n{payload.message}"
        send_telegram_alert(text, chat_id=payload.chat_id)

    # Store for the dashboard notifications endpoint
    _store_notification(payload.source, payload.severity, payload.title, payload.message)

    return {"status": "ok", "key": key}

# ── Recent notifications store ──────────────────────────────────────────────────
_recent_notifications: list[dict] = []

def get_recent_notifications(limit: int = 10) -> list[dict]:
    """Return the most recent notifications, newest first."""
    return list(reversed(_recent_notifications))[-limit:]

def _store_notification(source: str, severity: str, title: str, message: str):
    """Append to the in-memory recent list (used by status dashboard)."""
    _recent_notifications.append({
        "source": source,
        "severity": severity,
        "title": title,
        "message": message,
        "ts": time.time(),
    })
    # Keep last 100
    if len(_recent_notifications) > 100:
        _recent_notifications[:] = _recent_notifications[-100:]

