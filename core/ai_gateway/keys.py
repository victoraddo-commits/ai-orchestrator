"""API key management for the AI Gateway.

Simple bearer-token auth layer for machine consumers.  Separate from the JWT
session-token system used by dashboard users (core.authz) — these keys are
long-lived, revocable, and have no role/permission semantics (every key has the
same text_task access through the gateway).

Storage: memory/api_keys.json (gitignored, same atomic-write pattern as
provider_state.json).

Key format: "kai_" + 32 random bytes base64url-encoded.
Stored as SHA-256 hash — the plaintext is returned exactly once at creation.
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

STORAGE_PATH = Path(__file__).parent.parent.parent / "memory" / "api_keys.json"

_KEY_PREFIX = "kai_"
_KEY_BYTES = 32


@dataclass
class _ApiKeyRecord:
    key_id: str
    key_hash: str          # SHA-256 of the full plaintext key
    label: str             # human label set at creation
    created_at: str        # ISO-8601 timestamp


def _load() -> list[_ApiKeyRecord]:
    """Load all stored key records.  Returns empty list on any error."""
    try:
        if STORAGE_PATH.exists():
            raw = json.loads(STORAGE_PATH.read_text())
            entries = raw.get("keys", [])
            return [_ApiKeyRecord(**e) for e in entries]
    except (json.JSONDecodeError, OSError, TypeError):
        pass
    return []


def _save(records: list[_ApiKeyRecord]) -> None:
    """Atomic write — temp file + os.replace, same pattern as core.memory."""
    tmp = STORAGE_PATH.with_suffix(".tmp")
    data = {"keys": [{"key_id": r.key_id, "key_hash": r.key_hash,
                       "label": r.label, "created_at": r.created_at}
                      for r in records]}
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STORAGE_PATH)


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_api_key(label: str = "") -> tuple[str, str]:
    """Create a new API key.

    Returns (key_id, plaintext_key).  The plaintext is NOT stored — the caller
    is responsible for showing it to the user immediately.
    """
    plaintext = _KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
    key_id = plaintext[:16]  # first 16 chars as the stable identifier
    key_hash = _hash(plaintext)

    record = _ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        label=label,
        created_at=__import__("datetime").datetime.now().isoformat(),
    )

    records = _load()
    records.append(record)
    _save(records)

    return key_id, plaintext


def validate_api_key(plaintext: str) -> Optional[dict]:
    """Validate a bearer token.  Returns the key record (without hash) if
    valid, None otherwise.  Uses constant-time comparison."""
    if not plaintext or not plaintext.startswith(_KEY_PREFIX):
        return None

    key_hash = _hash(plaintext)
    records = _load()

    for r in records:
        if hmac.compare_digest(r.key_hash.encode(), key_hash.encode()):
            return {
                "key_id": r.key_id,
                "label": r.label,
                "created_at": r.created_at,
            }

    return None


def list_api_keys() -> list[dict]:
    """Return all keys (without hashes)."""
    return [
        {"key_id": r.key_id, "label": r.label, "created_at": r.created_at}
        for r in _load()
    ]


def revoke_api_key(key_id: str) -> bool:
    """Remove a key by its key_id prefix.  Returns True if found & removed."""
    records = _load()
    new_records = [r for r in records if r.key_id != key_id]
    if len(new_records) == len(records):
        return False
    _save(new_records)
    return True


def ensure_default_key() -> Optional[str]:
    """Create a default API key if none exists.  Returns the plaintext on first
    creation, None if keys already exist.  This lets the gateway work
    out-of-the-box without manual key management."""
    records = _load()
    if records:
        return None
    key_id, plaintext = generate_api_key(label="default (auto-created)")
    return plaintext
