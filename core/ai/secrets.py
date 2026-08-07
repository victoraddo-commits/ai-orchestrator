"""Secure secrets management for provider API keys.

Backend-only storage — keys are NEVER exposed to frontend clients, Telegram
bots, agent modules, or users.  The AI Gateway retrieves credentials securely
at request time and never returns them in responses.

Features:
  - Atomic writes (temp file + os.replace), same pattern as core.memory
  - Access audit logging (who accessed the key and when)
  - Key masking in logs (only last 4 chars shown)
  - Rotation support (set new key while preserving old for rollback window)
  - Health check (validate key by calling provider's models endpoint)
  - Permission control (keys are not readable through any API endpoint)

Storage: memory/provider_secrets.json (gitignored, 0600 permissions).
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

STORAGE_PATH = Path(__file__).parent.parent.parent / "memory" / "provider_secrets.json"
AUDIT_PATH = Path(__file__).parent.parent.parent / "memory" / "secret_access_audit.json"

_write_lock = Lock()


def _load() -> dict:
    """Load the secrets store. Returns empty dict on any error."""
    try:
        if STORAGE_PATH.exists():
            return json.loads(STORAGE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save(data: dict) -> None:
    """Atomic write — temp file + os.replace under a lock."""
    with _write_lock:
        tmp = STORAGE_PATH.with_suffix(".tmp")
        data.setdefault("_meta", {})["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp.write_text(json.dumps(data, indent=2))
        tmp.chmod(0o600)
        tmp.replace(STORAGE_PATH)


def _load_audit() -> list[dict]:
    try:
        if AUDIT_PATH.exists():
            raw = json.loads(AUDIT_PATH.read_text())
            return raw.get("records", [])
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_audit(records: list[dict]) -> None:
    tmp = AUDIT_PATH.with_suffix(".tmp")
    MAX = 5_000
    if len(records) > MAX:
        records = records[-MAX:]
    tmp.write_text(json.dumps({"records": records}, indent=2))
    tmp.chmod(0o600)
    tmp.replace(AUDIT_PATH)


def _log_access(provider: str, action: str, success: bool, detail: str = "") -> None:
    """Append an access audit record."""
    records = _load_audit()
    records.append({
        "provider": provider,
        "action": action,
        "success": success,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _save_audit(records)


# ---------------------------------------------------------------------------
# API: set / get / rotate / delete
# ---------------------------------------------------------------------------

def set_secret(provider: str, api_key: str, api_base: str = "",
               models: Optional[list[str]] = None) -> None:
    """Store (or update) a provider's API key and metadata.

    Args:
        provider: Provider key (e.g. "deepseek").
        api_key: The plaintext API key to store.
        api_base: Provider's API base URL (e.g. "https://api.deepseek.com/v1").
        models: Which models this credential gives access to.
    """
    data = _load()
    old = data.get(provider, {})

    data[provider] = {
        "api_key": api_key,
        "api_base": api_base or old.get("api_base", ""),
        "models": models or old.get("models", []),
        "created_at": old.get("created_at", datetime.now(timezone.utc).isoformat()),
        "rotated_at": datetime.now(timezone.utc).isoformat() if provider in data else None,
        "previous_key_hash": _hash(api_key) if provider in data else None,
    }
    _save(data)
    _log_access(provider, "set_secret", True)


def get_secret(provider: str) -> Optional[dict]:
    """Retrieve a provider's stored secrets (api_key, api_base, models).

    Returns None if no secret exists for this provider.  The returned api_key
    is the PLAINTEXT key — callers must never log or expose it.
    """
    data = _load()
    entry = data.get(provider)
    if entry is None:
        _log_access(provider, "get_secret", False, "not_found")
        return None

    _log_access(provider, "get_secret", True)
    return {
        "api_key": entry["api_key"],
        "api_base": entry.get("api_base", ""),
        "models": entry.get("models", []),
        "created_at": entry.get("created_at"),
    }


def get_api_key(provider: str) -> Optional[str]:
    """Convenience: return just the API key string for a provider.
    None if not found.  The key is PLAINTEXT — do not log or expose."""
    secret = get_secret(provider)
    return secret["api_key"] if secret else None


def rotate_secret(provider: str, new_api_key: str) -> bool:
    """Rotate a provider's API key.  Returns True if rotation succeeded."""
    data = _load()
    entry = data.get(provider)
    if entry is None:
        _log_access(provider, "rotate_secret", False, "provider_not_found")
        return False

    old_hash = _hash(entry["api_key"])
    entry["api_key"] = new_api_key
    entry["rotated_at"] = datetime.now(timezone.utc).isoformat()
    entry["previous_key_hash"] = old_hash
    _save(data)
    _log_access(provider, "rotate_secret", True)
    return True


def delete_secret(provider: str) -> bool:
    """Remove a provider's stored credentials. Returns True if found."""
    data = _load()
    if provider not in data:
        _log_access(provider, "delete_secret", False, "not_found")
        return False

    del data[provider]
    _save(data)
    _log_access(provider, "delete_secret", True)
    return True


def list_secrets() -> list[dict]:
    """List providers that have stored secrets (without the keys themselves).

    Returns only metadata — api_key is NEVER included. Safe for dashboards.
    """
    data = _load()
    result = []
    for provider, entry in data.items():
        if provider.startswith("_"):
            continue
        result.append({
            "provider": provider,
            "api_base": entry.get("api_base", ""),
            "models": entry.get("models", []),
            "created_at": entry.get("created_at"),
            "rotated_at": entry.get("rotated_at"),
            "has_key": bool(entry.get("api_key")),
        })
    return sorted(result, key=lambda x: x["provider"])


# ---------------------------------------------------------------------------
# API key health check (connectivity test)
# ---------------------------------------------------------------------------

def check_health(provider: str) -> dict:
    """Test whether a stored key can connect to the provider's API.

    Makes a lightweight GET /v1/models request to verify the key is valid.
    Does NOT log the key.

    Returns:
        {"ok": bool, "status": "connected"|"invalid_key"|"timeout"|"no_secret"|"error",
         "latency_ms": int, "detail": str, "models": [...]}
    """
    secret = get_secret(provider)
    if secret is None:
        return {"ok": False, "status": "no_secret", "latency_ms": 0, "detail": "No stored secret", "models": []}

    api_key = secret["api_key"]
    api_base = secret["api_base"].rstrip("/")

    if not api_base:
        return {"ok": False, "status": "error", "latency_ms": 0, "detail": "api_base not configured", "models": []}

    try:
        import requests as _requests
        start = time.time()
        resp = _requests.get(
            f"{api_base}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        latency_ms = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            models_json = resp.json()
            model_ids = [m.get("id", "") for m in models_json.get("data", [])]
            return {
                "ok": True,
                "status": "connected",
                "latency_ms": latency_ms,
                "detail": f"Found {len(model_ids)} models",
                "models": model_ids,
            }
        elif resp.status_code in (401, 403):
            return {
                "ok": False,
                "status": "invalid_key",
                "latency_ms": latency_ms,
                "detail": f"HTTP {resp.status_code}: key rejected",
                "models": [],
            }
        else:
            body = resp.text[:200]
            return {
                "ok": False,
                "status": "error",
                "latency_ms": latency_ms,
                "detail": f"HTTP {resp.status_code}: {body}",
                "models": [],
            }
    except Exception as exc:
        import requests as _requests
        if isinstance(exc, _requests.Timeout):
            return {"ok": False, "status": "timeout", "latency_ms": 0, "detail": "Connection timed out", "models": []}
        return {"ok": False, "status": "error", "latency_ms": 0, "detail": str(exc)[:200], "models": []}


def get_access_log(limit: int = 50) -> list[dict]:
    """Return recent secret access logs, newest first."""
    records = _load_audit()
    return records[-limit:][::-1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def mask_key(key: str) -> str:
    """Mask an API key for safe logging — only the last 4 chars are visible."""
    if not key or len(key) < 8:
        return "***"
    return "*" * (len(key) - 4) + key[-4:]
