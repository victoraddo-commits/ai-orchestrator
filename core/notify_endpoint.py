"""POST /notify — ecosystem alert endpoint (merged from kai-notify)."""
import time
import hashlib
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from core.telegram_bridge import send_telegram_alert

router = APIRouter(prefix="/notify", tags=["notifications"])

# Deduplication window in seconds (matches kai-notify 10min window)
DEDUP_WINDOW_SECS = 600
_rate_limit_store: dict[str, float] = {}  # source → last_sent timestamp

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
    # Source authentication (bearer token — same pattern as kai-notify)
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing authorization")

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

    return {"status": "ok", "key": key}

# In-memory dedup store (per-process; resets on restart — acceptable for alert deduplication)
_dedup_store: dict[str, float] = {}
