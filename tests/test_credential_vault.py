"""Tests for AI-7: Credential Vault — AES-256-GCM encrypted key storage.

Covers: encrypt/decrypt, store/retrieve/delete, rotation detection,
migration of plaintext keys, vault status, and health checks.
"""

import base64
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_crypto():
    """Ensure cryptography is available for all tests."""
    import core.ai.credential_vault as vault

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(vault, "_HAS_CRYPTO", True)


@pytest.fixture
def master_key_set():
    """Ensure a known master key is loaded."""
    import core.ai.credential_vault as vault

    key = bytes.fromhex("a" * 64)  # 32 bytes
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(vault, "_MASTER_KEY", key)
    monkeypatch.setattr(vault, "_MASTER_KEY_SOURCE", "test")
    return key


@pytest.fixture
def empty_secrets(monkeypatch):
    """Start with an empty in-memory secrets store."""
    import core.ai.credential_vault as vault

    store = {}

    def fake_load():
        return store

    def fake_save(data):
        store.clear()
        store.update(data)

    def fake_set(provider, api_key, api_base="", models=None):
        store[provider] = {
            "api_key": api_key,
            "api_base": api_base,
            "models": models or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def fake_get(provider):
        return store.get(provider)

    def fake_list():
        return [
            {"provider": p, "created_at": v.get("created_at")}
            for p, v in store.items()
            if not p.startswith("_")
        ]

    def fake_delete(provider):
        return store.pop(provider, None) is not None

    monkeypatch.setattr(vault._secrets_store, "_load", fake_load)
    monkeypatch.setattr(vault._secrets_store, "_save", fake_save)
    monkeypatch.setattr(vault._secrets_store, "set_secret", fake_set)
    monkeypatch.setattr(vault._secrets_store, "get_secret", fake_get)
    monkeypatch.setattr(vault._secrets_store, "list_secrets", fake_list)
    monkeypatch.setattr(vault._secrets_store, "delete_secret", fake_delete)
    monkeypatch.setattr(vault, "_load_rotation_state", lambda: {})
    monkeypatch.setattr(vault, "_save_rotation_state", lambda s: None)


# ---------------------------------------------------------------------------
# Encrypt / Decrypt
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    def test_roundtrip(self, master_key_set):
        from core.ai.credential_vault import encrypt, decrypt

        plaintext = "sk-api-1234567890abcdef"
        encrypted = encrypt(plaintext)
        assert encrypted != plaintext
        assert len(encrypted) > 0
        result = decrypt(encrypted)
        assert result == plaintext

    def test_different_ciphertexts(self, master_key_set):
        from core.ai.credential_vault import encrypt

        e1 = encrypt("key-a")
        e2 = encrypt("key-a")
        # Same plaintext should produce different ciphertext (different nonce)
        assert e1 != e2

    def test_special_characters(self, master_key_set):
        from core.ai.credential_vault import encrypt, decrypt

        plaintext = "key\nwith\ttabs and \"quotes\" and emoji \U0001f680"
        encrypted = encrypt(plaintext)
        result = decrypt(encrypted)
        assert result == plaintext

    def test_long_key(self, master_key_set):
        from core.ai.credential_vault import encrypt, decrypt

        plaintext = "sk-" + "x" * 500
        encrypted = encrypt(plaintext)
        result = decrypt(encrypted)
        assert result == plaintext

    def test_decrypt_wrong_key_fails(self, master_key_set):
        import core.ai.credential_vault as vault
        from core.ai.credential_vault import encrypt

        encrypted = encrypt("secret-key")

        # Switch to a different master key
        wrong_key = bytes.fromhex("b" * 64)
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vault, "_MASTER_KEY", wrong_key)

        with pytest.raises(Exception):
            vault.decrypt(encrypted)

    def test_decrypt_without_crypto_raises(self, master_key_set):
        import core.ai.credential_vault as vault

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vault, "_HAS_CRYPTO", False)

        with pytest.raises(RuntimeError, match="not installed"):
            vault.decrypt("anything")

    def test_encrypt_without_crypto_raises(self, master_key_set):
        import core.ai.credential_vault as vault

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vault, "_HAS_CRYPTO", False)

        with pytest.raises(RuntimeError, match="not installed"):
            vault.encrypt("anything")


# ---------------------------------------------------------------------------
# Store / Retrieve / Delete
# ---------------------------------------------------------------------------


class TestStoreRetrieveDelete:
    def test_store_and_retrieve(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, retrieve_credential, retrieve_api_key,
        )

        store_credential(
            "test_provider",
            "sk-test-abc123",
            "https://api.test.com/v1",
            ["model-a", "model-b"],
        )

        cred = retrieve_credential("test_provider")
        assert cred is not None
        assert cred["api_key"] == "sk-test-abc123"
        assert cred["api_base"] == "https://api.test.com/v1"
        assert cred["models"] == ["model-a", "model-b"]

        # Convenience function
        assert retrieve_api_key("test_provider") == "sk-test-abc123"

    def test_retrieve_nonexistent(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import retrieve_credential, retrieve_api_key

        assert retrieve_credential("nonexistent") is None
        assert retrieve_api_key("nonexistent") is None

    def test_list_entries(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, list_vault_entries,
        )

        store_credential("provider-a", "key-a", "https://a.com")
        store_credential("provider-b", "key-b", "https://b.com")

        entries = list_vault_entries()
        providers = {e["provider"] for e in entries}
        assert "provider-a" in providers
        assert "provider-b" in providers

        # Keys should NOT appear in list
        for e in entries:
            assert "api_key" not in e

    def test_delete_entry(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, retrieve_credential, delete_vault_entry,
        )

        store_credential("to_delete", "delete-me", "https://del.com")

        assert retrieve_credential("to_delete") is not None
        result = delete_vault_entry("to_delete")
        assert result is True
        assert retrieve_credential("to_delete") is None

    def test_delete_nonexistent(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import delete_vault_entry

        assert delete_vault_entry("nonexistent") is False

    def test_store_twice_overwrites(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, retrieve_credential,
        )

        store_credential("twice", "first-key")
        store_credential("twice", "second-key")

        cred = retrieve_credential("twice")
        assert cred["api_key"] == "second-key"


# ---------------------------------------------------------------------------
# Rotation management
# ---------------------------------------------------------------------------


class TestRotation:
    def test_new_credential_not_due(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, check_rotation_needed,
        )

        store_credential("fresh", "fresh-key")
        due = check_rotation_needed()
        providers = {d["provider"] for d in due}
        # Freshly stored key should not be due
        assert "fresh" not in providers

    def test_old_credential_shows_due(self, master_key_set, monkeypatch):
        """Simulate a credential stored 91 days ago."""
        import core.ai.credential_vault as vault

        old_date = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()

        def fake_list():
            return [{"provider": "old_provider", "created_at": old_date}]

        monkeypatch.setattr(vault, "list_vault_entries", fake_list)
        monkeypatch.setattr(vault, "_load_rotation_state", lambda: {})
        monkeypatch.setattr(vault, "_save_rotation_state", lambda s: None)

        due = vault.check_rotation_needed()
        providers = {d["provider"] for d in due}
        assert "old_provider" in providers

    def test_exactly_90_days_shows_due(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

        def fake_list():
            return [{"provider": "borderline", "created_at": old_date}]

        monkeypatch.setattr(vault, "list_vault_entries", fake_list)
        monkeypatch.setattr(vault, "_load_rotation_state", lambda: {})
        monkeypatch.setattr(vault, "_save_rotation_state", lambda s: None)

        due = vault.check_rotation_needed()
        assert any(d["provider"] == "borderline" for d in due)

    def test_mark_rotated_updates_timestamp(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import (
            store_credential, mark_rotated, check_rotation_needed,
        )

        store_credential("rotatable", "key")
        mark_rotated("rotatable")

        due = check_rotation_needed()
        # After marking as rotated, should not be due
        assert not any(d["provider"] == "rotatable" for d in due)

    def test_empty_vault_no_rotation(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "list_vault_entries", lambda: [])
        monkeypatch.setattr(vault, "_load_rotation_state", lambda: {})
        monkeypatch.setattr(vault, "_save_rotation_state", lambda s: None)

        due = vault.check_rotation_needed()
        assert due == []


# ---------------------------------------------------------------------------
# Vault status
# ---------------------------------------------------------------------------


class TestVaultStatus:
    def test_returns_configuration(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import get_vault_status

        status = get_vault_status()
        assert status["encryption"] == "AES-256-GCM"
        assert status["rotation_days"] == 90
        assert status["overlap_days"] == 7
        assert isinstance(status["total_credentials"], int)
        assert isinstance(status["rotation_due"], int)
        assert status["crypto_available"] is True

    def test_shows_unavailable_when_no_crypto(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "_HAS_CRYPTO", False)

        status = vault.get_vault_status()
        assert status["encryption"] == "unavailable"
        assert status["crypto_available"] is False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_plaintext_key_encrypted(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        store = {
            "test_provider": {
                "api_key": "sk-plaintext-key",
                "api_base": "https://api.test.com",
                "models": [],
            }
        }
        saved = {}

        monkeypatch.setattr(vault._secrets_store, "_load", lambda: dict(store))
        monkeypatch.setattr(vault._secrets_store, "_save", lambda data: saved.update(data) or store.update(data))

        result = vault.migrate_plaintext_keys()
        assert result["migrated"] == 1
        assert result["errors"] == 0

        # The stored key should now be encrypted (base64, not plaintext)
        saved_key = store["test_provider"]["api_key"]
        assert saved_key != "sk-plaintext-key"
        # Should be valid base64
        decoded = base64.b64decode(saved_key)
        assert len(decoded) >= 28

    def test_already_encrypted_skipped(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault
        from core.ai.credential_vault import encrypt

        already = encrypt("already-encrypted-key")
        store = {
            "test_provider": {
                "api_key": already,
                "api_base": "https://api.test.com",
                "models": [],
            }
        }

        monkeypatch.setattr(vault._secrets_store, "_load", lambda: dict(store))
        monkeypatch.setattr(vault._secrets_store, "_save", lambda data: None)

        result = vault.migrate_plaintext_keys()
        assert result["migrated"] == 0
        assert result["skipped"] >= 1
        assert store["test_provider"]["api_key"] == already  # Unchanged

    def test_empty_key_skipped(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        store = {
            "test_provider": {
                "api_key": "",
                "api_base": "https://api.test.com",
                "models": [],
            }
        }

        monkeypatch.setattr(vault._secrets_store, "_load", lambda: dict(store))
        monkeypatch.setattr(vault._secrets_store, "_save", lambda data: None)

        result = vault.migrate_plaintext_keys()
        assert result["migrated"] == 0

    def test_migration_no_crypto(self, master_key_set, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "_HAS_CRYPTO", False)

        result = vault.migrate_plaintext_keys()
        assert result["migrated"] == 0
        assert result["detail"] == "cryptography library not installed"


# ---------------------------------------------------------------------------
# Master key loading
# ---------------------------------------------------------------------------


class TestMasterKey:
    def test_load_from_env(self, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "_MASTER_KEY", None)
        monkeypatch.setattr(vault, "_MASTER_KEY_SOURCE", "none")
        monkeypatch.setenv("VAULT_MASTER_KEY", "c" * 64)

        key = vault._load_master_key()
        assert key is not None
        assert len(key) == 32
        assert vault._MASTER_KEY_SOURCE == "env"

    def test_load_invalid_env_key(self, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "_MASTER_KEY", None)
        monkeypatch.setattr(vault, "_MASTER_KEY_SOURCE", "none")
        monkeypatch.setenv("VAULT_MASTER_KEY", "not-valid-hex!!")

        # Should fall through to file load or generation
        # No assertion on source, just verify it doesn't crash
        key = vault._load_master_key()
        assert key is not None

    def test_get_master_key_raises_when_none(self, monkeypatch):
        import core.ai.credential_vault as vault

        monkeypatch.setattr(vault, "_MASTER_KEY", None)
        monkeypatch.setattr(vault, "_MASTER_KEY_SOURCE", "none")
        # Also patch _load_master_key to return None
        monkeypatch.setattr(vault, "_load_master_key", lambda: None)

        with pytest.raises(RuntimeError, match="unavailable"):
            vault._get_master_key()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_no_secret_returns_error(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import check_health

        result = check_health("nonexistent")
        assert result["ok"] is False
        assert result["status"] == "no_secret"

    def test_no_api_base_returns_error(self, master_key_set, empty_secrets):
        from core.ai.credential_vault import store_credential, check_health

        store_credential("no_base", "key", api_base="")

        result = check_health("no_base")
        assert result["ok"] is False
        assert "api_base" in result["detail"]

    def test_health_check_success(self, master_key_set, empty_secrets, monkeypatch):
        from core.ai.credential_vault import store_credential, check_health

        store_credential("test_p", "sk-key", api_base="https://api.test.com")

        # Mock the HTTP request
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"id": "model-1"}, {"id": "model-2"}]
        }

        def fake_get(url, headers=None, timeout=None):
            return mock_resp

        monkeypatch.setattr(
            "requests.get", fake_get
        )

        result = check_health("test_p")
        assert result["ok"] is True
        assert result["status"] == "connected"
        assert result["models"] == ["model-1", "model-2"]

    def test_health_check_invalid_key(self, master_key_set, empty_secrets, monkeypatch):
        from core.ai.credential_vault import store_credential, check_health

        store_credential("bad_key", "sk-bad", api_base="https://api.test.com")

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        def fake_get(url, headers=None, timeout=None):
            return mock_resp

        monkeypatch.setattr(
            "requests.get", fake_get
        )

        result = check_health("bad_key")
        assert result["ok"] is False
        assert result["status"] == "invalid_key"

    def test_health_check_timeout(self, master_key_set, empty_secrets, monkeypatch):
        from core.ai.credential_vault import store_credential, check_health

        store_credential("timeout_p", "sk-key", api_base="https://api.test.com")

        import requests as _requests

        def fake_get(url, headers=None, timeout=None):
            raise _requests.Timeout("Connection timed out")

        monkeypatch.setattr(
            "requests.get", fake_get
        )

        result = check_health("timeout_p")
        assert result["ok"] is False
        assert result["status"] == "timeout"

    def test_health_check_connection_error(self, master_key_set, empty_secrets,
                                            monkeypatch):
        from core.ai.credential_vault import store_credential, check_health

        store_credential("conn_err", "sk-key", api_base="https://api.test.com")

        def fake_get(url, headers=None, timeout=None):
            raise ConnectionError("Refused")

        monkeypatch.setattr(
            "requests.get", fake_get
        )

        result = check_health("conn_err")
        assert result["ok"] is False
        # Should not crash — any non-timeout error becomes "error" status
        assert isinstance(result["status"], str)
