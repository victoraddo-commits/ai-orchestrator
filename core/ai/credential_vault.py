"""AI-7: Credential Vault — AES-256-GCM encrypted key storage.

Extends 17E-2 Proxmox credential vault pattern with:
  - AES-256-GCM encryption at rest (keys are never stored as plaintext)
  - Automated 90-day rotation detection with overlap period
  - Access audit logging (extends existing secrets module audit)
  - Migration path for existing plaintext keys in provider_secrets.json

The master encryption key is read from VAULT_MASTER_KEY env var on startup.
If not set, a random key is generated and logged ONCE — back it up immediately.

Dependencies: core.ai.secrets (underlying storage + audit), cryptography library.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from core.ai import secrets as _secrets_store
from core.logger import info as _info, warning as _warn

# ---------------------------------------------------------------------------
# Cryptographic primitives (AES-256-GCM)
# ---------------------------------------------------------------------------

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False
    AESGCM = None  # type: ignore


# Master key — loaded once at module import, never rotated automatically.
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
_MASTER_KEY: Optional[bytes] = None
_MASTER_KEY_SOURCE: str = "none"


def _load_master_key() -> Optional[bytes]:
    """Load the vault master key from environment or generate one."""
    global _MASTER_KEY, _MASTER_KEY_SOURCE

    env_key = os.environ.get("VAULT_MASTER_KEY", "").strip()
    if env_key:
        try:
            key_bytes = bytes.fromhex(env_key)
            if len(key_bytes) == 32:
                _MASTER_KEY = key_bytes
                _MASTER_KEY_SOURCE = "env"
                return key_bytes
            else:
                _warn(f"VAULT_MASTER_KEY has wrong length ({len(key_bytes)} bytes), need 32")
        except ValueError:
            _warn("VAULT_MASTER_KEY is not valid hex")

    # Try to load from disk
    key_path = Path("memory/vault_master_key")
    if key_path.exists():
        try:
            key_bytes = bytes.fromhex(key_path.read_text().strip())
            if len(key_bytes) == 32:
                _MASTER_KEY = key_bytes
                _MASTER_KEY_SOURCE = "file"
                return key_bytes
        except (ValueError, OSError):
            pass

    # Generate and persist
    if _HAS_CRYPTO:
        key_bytes = AESGCM.generate_key(bit_length=256)  # 32 bytes
    else:
        key_bytes = _secrets.token_bytes(32)

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key_bytes.hex())
    key_path.chmod(0o600)
    _MASTER_KEY_SOURCE = "generated"
    _info(
        "Vault master key generated and saved to memory/vault_master_key. "
        "Back up this file — losing it means losing all stored credentials."
    )

    _MASTER_KEY = key_bytes
    return key_bytes


def _get_master_key() -> bytes:
    global _MASTER_KEY
    if _MASTER_KEY is None:
        _load_master_key()
    if _MASTER_KEY is None:
        raise RuntimeError("Credential vault master key unavailable")
    return _MASTER_KEY


# ---------------------------------------------------------------------------
# Encrypt / Decrypt
# ---------------------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string with AES-256-GCM.

    Returns a base64-encoded ciphertext containing (nonce || ciphertext).
    The nonce is 12 bytes (96 bits) as recommended for GCM.
    """
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography library not installed — cannot encrypt")

    key = _get_master_key()
    aesgcm = AESGCM(key)
    nonce = _secrets.token_bytes(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Prepend nonce to ciphertext for storage
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt(encrypted: str) -> str:
    """Decrypt a value previously encrypted with encrypt()."""
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography library not installed — cannot decrypt")

    key = _get_master_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted.encode("ascii"))
    nonce = combined[:12]
    ciphertext = combined[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# Vault API — encrypted wrapper around secrets.py
# ---------------------------------------------------------------------------

# Rotation window: keys older than this should be rotated
ROTATION_DAYS = 90
# Overlap period: how long to keep the old key valid after rotation
OVERLAP_DAYS = 7

ROTATION_STATE_PATH = Path("memory/vault_rotation_state.json")


def _load_rotation_state() -> dict:
    try:
        if ROTATION_STATE_PATH.exists():
            return json.loads(ROTATION_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_rotation_state(state: dict) -> None:
    ROTATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROTATION_STATE_PATH.write_text(json.dumps(state, indent=2))
    ROTATION_STATE_PATH.chmod(0o600)


def store_credential(provider: str, api_key: str, api_base: str = "",
                     models: Optional[list[str]] = None) -> None:
    """Store an encrypted credential for a provider.

    The key is encrypted with AES-256-GCM before storage. The underlying
    secrets module handles audit logging and atomic writes.
    """
    encrypted_key = encrypt(api_key)
    _secrets_store.set_secret(provider, encrypted_key, api_base, models)

    # Record rotation timestamp
    state = _load_rotation_state()
    state[provider] = {
        "last_rotated": datetime.now(timezone.utc).isoformat(),
        "next_rotation_due": (
            datetime.now(timezone.utc) + timedelta(days=ROTATION_DAYS)
        ).isoformat(),
    }
    _save_rotation_state(state)


def retrieve_credential(provider: str) -> Optional[dict]:
    """Retrieve a provider's credential.

    Source order (add-only, 2026-08-22): kai-vault machine plane first;
    local AES-GCM store as fallback when the vault has no value or is
    unreachable. The returned dict gains a "source" key for auditing —
    values themselves are never logged anywhere.
    """
    stored = _secrets_store.get_secret(provider)
    plaintext_key = None
    source = "local-vault"

    if stored is not None:
        encrypted_key = stored["api_key"]
        try:
            plaintext_key = decrypt(encrypted_key)
        except Exception:
            # Key might already be plaintext (pre-vault migration)
            plaintext_key = encrypted_key

    try:
        from core.ai.kai_vault_client import fetch_for_provider
        vault_value = fetch_for_provider(provider)
        if vault_value:
            plaintext_key = vault_value
            source = "kai-vault"
    except Exception:
        pass  # vault trouble never blocks credential resolution

    if plaintext_key is None:
        return None

    return {
        "api_key": plaintext_key,
        "api_base": (stored or {}).get("api_base", ""),
        "models": (stored or {}).get("models", []),
        "created_at": (stored or {}).get("created_at"),
        "source": source,
    }


def retrieve_api_key(provider: str) -> Optional[str]:
    """Convenience: return just the plaintext API key."""
    cred = retrieve_credential(provider)
    return cred["api_key"] if cred else None


def list_vault_entries() -> list[dict]:
    """List all vault entries (metadata only, no keys)."""
    return _secrets_store.list_secrets()


def delete_vault_entry(provider: str) -> bool:
    """Delete a credential from the vault."""
    result = _secrets_store.delete_secret(provider)

    # Clean up rotation state
    state = _load_rotation_state()
    if provider in state:
        del state[provider]
        _save_rotation_state(state)

    return result


def check_health(provider: str) -> dict:
    """Test a stored credential by calling the provider's models endpoint.

    Same as secrets.check_health() but decrypts the key first.
    """
    # Temporarily swap the encrypted key with the decrypted one for the check
    cred = retrieve_credential(provider)
    if cred is None:
        return {"ok": False, "status": "no_secret", "latency_ms": 0,
                "detail": "No stored credential", "models": []}

    # Use the plaintext key directly through the secrets health check path
    api_key = cred["api_key"]
    api_base = cred["api_base"].rstrip("/")

    if not api_base:
        return {"ok": False, "status": "error", "latency_ms": 0,
                "detail": "api_base not configured", "models": []}

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
                "ok": True, "status": "connected", "latency_ms": latency_ms,
                "detail": f"Found {len(model_ids)} models", "models": model_ids,
            }
        elif resp.status_code in (401, 403):
            return {
                "ok": False, "status": "invalid_key", "latency_ms": latency_ms,
                "detail": f"HTTP {resp.status_code}: key rejected", "models": [],
            }
        else:
            body = resp.text[:200]
            return {
                "ok": False, "status": "error", "latency_ms": latency_ms,
                "detail": f"HTTP {resp.status_code}: {body}", "models": [],
            }
    except Exception as exc:
        import requests as _requests
        if isinstance(exc, _requests.Timeout):
            return {"ok": False, "status": "timeout", "latency_ms": 0,
                    "detail": "Connection timed out", "models": []}
        return {"ok": False, "status": "error", "latency_ms": 0,
                "detail": str(exc)[:200], "models": []}


# ---------------------------------------------------------------------------
# Rotation management
# ---------------------------------------------------------------------------

def check_rotation_needed() -> list[dict]:
    """Check all stored credentials for rotation eligibility.

    Returns a list of providers whose keys are due (or overdue) for rotation.
    A key is due when it's older than ROTATION_DAYS (90 days).
    """
    state = _load_rotation_state()
    entries = list_vault_entries()
    now = datetime.now(timezone.utc)
    due = []

    for entry in entries:
        provider = entry["provider"]
        rot = state.get(provider, {})
        created_at = entry.get("created_at")

        # Compute age from creation date
        age_days = None
        if created_at:
            try:
                created = datetime.fromisoformat(created_at)
                age_days = (now - created).days
            except (ValueError, TypeError):
                pass

        due_date = rot.get("next_rotation_due")
        is_overdue = False
        if due_date:
            try:
                is_overdue = datetime.fromisoformat(due_date) <= now
            except (ValueError, TypeError):
                pass

        if age_days is not None and age_days >= ROTATION_DAYS:
            is_overdue = True

        due.append({
            "provider": provider,
            "age_days": age_days,
            "last_rotated": rot.get("last_rotated"),
            "next_rotation_due": rot.get("next_rotation_due"),
            "overdue": is_overdue,
        })

    return [d for d in due if d["overdue"]]


def mark_rotated(provider: str) -> None:
    """Update rotation timestamp after a key has been rotated."""
    state = _load_rotation_state()
    state[provider] = {
        "last_rotated": datetime.now(timezone.utc).isoformat(),
        "next_rotation_due": (
            datetime.now(timezone.utc) + timedelta(days=ROTATION_DAYS)
        ).isoformat(),
    }
    _save_rotation_state(state)


# ---------------------------------------------------------------------------
# Migration: encrypt existing plaintext keys
# ---------------------------------------------------------------------------

def migrate_plaintext_keys() -> dict:
    """One-time migration: encrypt any plaintext keys still in storage.

    Scans provider_secrets.json for keys that aren't base64-encoded
    AES-GCM ciphertexts and encrypts them. Safe to run multiple times —
    already-encrypted keys are skipped.

    Returns:
        {"migrated": int, "skipped": int, "errors": int}
    """
    if not _HAS_CRYPTO:
        return {"migrated": 0, "skipped": 0, "errors": 0,
                "detail": "cryptography library not installed"}

    data = _secrets_store._load()
    migrated = 0
    skipped = 0
    errors = 0

    for provider, entry in list(data.items()):
        if provider.startswith("_"):
            continue

        api_key = entry.get("api_key", "")
        if not api_key:
            skipped += 1
            continue

        # Check if already encrypted (valid base64 of sufficient length)
        try:
            decoded = base64.b64decode(api_key)
            if len(decoded) >= 28:  # 12 nonce + 16+ ciphertext
                skipped += 1
                continue
        except Exception:
            pass

        # Encrypt the plaintext key
        try:
            encrypted_key = encrypt(api_key)
            entry["api_key"] = encrypted_key
            entry["_migrated_at"] = datetime.now(timezone.utc).isoformat()
            data[provider] = entry
            migrated += 1
        except Exception as exc:
            _warn(f"Failed to encrypt key for {provider}: {exc}")
            errors += 1

    if migrated > 0:
        _secrets_store._save(data)
        _info(f"Vault migration: encrypted {migrated} keys, skipped {skipped}, errors {errors}")

    return {"migrated": migrated, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Vault status
# ---------------------------------------------------------------------------

def get_vault_status() -> dict:
    """Return vault health and configuration."""
    global _MASTER_KEY, _MASTER_KEY_SOURCE
    # Resolve the master key lazily so the reported source reflects reality.
    # Before this, a fresh process reported the module default "none" until
    # the first encrypt/decrypt ran — which once read as an ephemeral-key
    # risk in the 2026-08-22 ecosystem audit when the key was actually
    # persisted at memory/vault_master_key (0600).
    if _MASTER_KEY is None:
        _load_master_key()
    return {
        "encryption": "AES-256-GCM" if _HAS_CRYPTO else "unavailable",
        "master_key_source": _MASTER_KEY_SOURCE,
        "rotation_days": ROTATION_DAYS,
        "overlap_days": OVERLAP_DAYS,
        "total_credentials": len(list_vault_entries()),
        "rotation_due": len(check_rotation_needed()),
        "crypto_available": _HAS_CRYPTO,
    }
