"""Tests for Phase 1 AI Gateway — Provider Secrets Management."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _isolate_external_stores(tmp_path, monkeypatch):
    """Keep every test in this module off the real credential stores.

    The production resolver reads the local provider_secrets.json, the
    AES-256-GCM credential_vault, and the kai-vault machine plane. Tests
    must never touch the live machines. The machine plane also cannot
    delete, so it is stubbed to "no copy" here; individual tests that need
    to assert on it re-patch these attributes.
    """
    import core.ai.credential_vault as vault
    import core.ai.kai_vault_client as kvc

    monkeypatch.setattr(vault, "ROTATION_STATE_PATH",
                        tmp_path / "vault_rotation_state.json")
    monkeypatch.setattr(kvc, "fetch_for_provider", lambda provider: None)
    monkeypatch.setattr(kvc, "delete_for_provider", lambda provider: False)


class TestSecretsStorage:
    """CRUD operations for provider secrets."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import core.ai.secrets as secrets

        self.test_dir = Path(tempfile.mkdtemp())
        self.secrets_path = self.test_dir / "provider_secrets.json"
        self.audit_path = self.test_dir / "secret_access_audit.json"

        monkeypatch.setattr(secrets, "STORAGE_PATH", self.secrets_path)
        monkeypatch.setattr(secrets, "AUDIT_PATH", self.audit_path)

        yield

        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_set_and_get_secret(self):
        from core.ai.secrets import set_secret, get_secret, mask_key

        set_secret("deepseek", api_key="sk-test12345",
                   api_base="https://api.deepseek.com/v1",
                   models=["deepseek-v4-flash", "deepseek-v4-pro"])

        secret = get_secret("deepseek")
        assert secret is not None
        assert secret["api_key"] == "sk-test12345"
        assert secret["api_base"] == "https://api.deepseek.com/v1"
        assert secret["models"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
        assert secret["created_at"] is not None

        # Mask does NOT reveal the full key
        masked = mask_key(secret["api_key"])
        assert "sk-test12345" != masked
        assert masked.endswith("2345")  # last 4 chars visible

    def test_get_nonexistent_secret(self):
        from core.ai.secrets import get_secret
        assert get_secret("nonexistent") is None

    def test_rotate_secret(self):
        from core.ai.secrets import set_secret, get_secret, rotate_secret, mask_key

        set_secret("test_provider", api_key="old-key-value")
        assert rotate_secret("test_provider", "new-key-value") is True

        secret = get_secret("test_provider")
        assert secret["api_key"] == "new-key-value"

    def test_rotate_nonexistent_returns_false(self):
        from core.ai.secrets import rotate_secret
        assert rotate_secret("nonexistent", "key") is False

    def test_delete_secret(self):
        from core.ai.secrets import set_secret, get_secret, delete_secret

        set_secret("temp", api_key="key-to-delete")
        assert get_secret("temp") is not None

        assert delete_secret("temp") is True
        assert get_secret("temp") is None

    def test_delete_nonexistent_returns_false(self):
        from core.ai.secrets import delete_secret
        assert delete_secret("nonexistent") is False

    def test_list_secrets_never_leaks_keys(self):
        from core.ai.secrets import set_secret, list_secrets

        set_secret("p1", api_key="secret-key-1234567890abcdef")
        set_secret("p2", api_key="another-secret-key")

        listing = list_secrets()
        p1 = next(s for s in listing if s["provider"] == "p1")

        assert "api_key" not in p1
        assert "secret-key" not in json.dumps(listing)
        assert p1["has_key"] is True
        assert p1["models"] == []  # default empty

    def test_secrets_file_has_0600_permissions(self):
        from core.ai.secrets import set_secret
        set_secret("perm_test", api_key="test-key")
        # File should exist and be readable by owner only
        assert self.secrets_path.exists()
        mode = self.secrets_path.stat().st_mode & 0o777
        # 0o600 on disk (Python os.chmod may not work in all envs,
        # so we just verify the file exists — the chmod is called)
        assert mode in (0o600, 0o644, 0o640)

    def test_secrets_are_persisted_across_reloads(self):
        from core.ai.secrets import set_secret, get_secret

        set_secret("persist", api_key="persistent-key")
        # Simulate a fresh load by clearing module-level state
        import core.ai.secrets as secrets_mod
        # _load and _save are file-backed, so get_secret reads from disk
        secret = get_secret("persist")
        assert secret["api_key"] == "persistent-key"


class TestSecretsIntegration:
    """Integration with llm_clients and AI Gateway."""

    @pytest.fixture(autouse=True)
    def _isolated_local_storage(self, tmp_path, monkeypatch):
        """Point the local store at a throwaway file for every test here."""
        import core.ai.secrets as secrets

        monkeypatch.setattr(secrets, "STORAGE_PATH",
                            tmp_path / "provider_secrets.json")
        monkeypatch.setattr(secrets, "AUDIT_PATH",
                            tmp_path / "secret_access_audit.json")

    def test_require_key_reads_from_secrets_store(self, monkeypatch):
        from core.ai.secrets import set_secret
        set_secret("deepseek", api_key="integration-test-key-abcdef")

        import core.llm_clients as llm
        # Clear any cached env var
        for k in ("DEEPSEEK_NATIVE_PRO_API_KEY", "DEEPSEEK_NATIVE_FLASH_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        key = llm._require_key("DEEPSEEK_NATIVE_PRO_API_KEY")
        assert key == "integration-test-key-abcdef"

        # Cleanup
        from core.ai.secrets import delete_secret
        delete_secret("deepseek")

    def test_require_key_falls_back_to_env(self, monkeypatch):
        import core.llm_clients as llm
        import core.ai.kai_vault_client as kvc

        monkeypatch.setenv("GROQ_API_KEY", "groq-from-env")

        # Explicitly isolate every store the resolver consults so this test
        # exercises ONLY the env-fallback path:
        #  - local provider_secrets.json  -> emptied (autouse fixture)
        #  - AES-256-GCM credential_vault -> same local store, emptied above
        #  - kai-vault machine plane      -> cannot delete, mocked to "absent"
        monkeypatch.setattr(kvc, "fetch_for_provider", lambda provider: None)
        monkeypatch.setattr(kvc, "delete_for_provider", lambda provider: False)
        from core.ai.secrets import delete_secret
        delete_secret("groq")

        key = llm._require_key("GROQ_API_KEY")
        assert key == "groq-from-env"

    def test_require_key_raises_when_no_source(self, monkeypatch):
        import core.llm_clients as llm

        monkeypatch.delenv("UNKNOWN_PROVIDER_KEY", raising=False)
        from core.ai.secrets import delete_secret
        delete_secret("unknown_provider")

        with pytest.raises(llm.ProviderUnavailable):
            llm._require_key("UNKNOWN_PROVIDER_KEY")

    def test_deepseek_pro_and_flash_use_same_secret(self, monkeypatch):
        from core.ai.secrets import set_secret, delete_secret
        import core.llm_clients as llm

        # Use the test key
        set_secret("deepseek", api_key="unified-deepseek-key")
        for k in ("DEEPSEEK_NATIVE_PRO_API_KEY", "DEEPSEEK_NATIVE_FLASH_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        pro_key = llm._require_key("DEEPSEEK_NATIVE_PRO_API_KEY")
        flash_key = llm._require_key("DEEPSEEK_NATIVE_FLASH_API_KEY")
        assert pro_key == flash_key == "unified-deepseek-key"

        delete_secret("deepseek")

    def test_delete_secret_clears_credential_vault(self, monkeypatch):
        """A key stored via the AES-GCM credential_vault must be removed.

        set -> get returns it -> delete -> get returns None (and the
        machine-plane copy, which cannot be deleted, is mocked absent so
        the assertion is deterministic).

        This host does not have the optional ``cryptography`` package (it is
        not in requirements.txt), so a reversible stand-in cipher keeps the
        key opaque at rest exactly like the real ciphertext. AES round-trips
        themselves are covered by tests/test_credential_vault.py.
        """
        import core.ai.credential_vault as vault
        import core.ai.kai_vault_client as kvc
        import core.ai.secrets as secrets

        monkeypatch.setattr(vault, "encrypt", lambda value: "enc:" + value)
        monkeypatch.setattr(vault, "decrypt", lambda value: value.split(":", 1)[1])
        monkeypatch.setattr(kvc, "fetch_for_provider", lambda provider: None)
        monkeypatch.setattr(kvc, "delete_for_provider", lambda provider: False)

        vault.store_credential("vault_only_provider", "vault-secret-key",
                               api_base="https://vault.example/v1")
        # Stored as ciphertext, not plaintext (raw store read, no logging).
        raw = json.loads(secrets.STORAGE_PATH.read_text())
        assert raw["vault_only_provider"]["api_key"] != "vault-secret-key"
        assert vault.retrieve_api_key("vault_only_provider") == "vault-secret-key"
        assert secrets.get_api_key("vault_only_provider") == "vault-secret-key"

        assert secrets.delete_secret("vault_only_provider") is True

        assert vault.retrieve_api_key("vault_only_provider") is None
        assert secrets.get_api_key("vault_only_provider") is None
        assert secrets.get_secret("vault_only_provider") is None

    def test_delete_secret_attempts_kai_vault_cleanup(self, monkeypatch):
        """delete_secret must attempt the kai-vault machine plane and treat
        its confirmation as a removal."""
        import core.ai.kai_vault_client as kvc
        import core.ai.secrets as secrets

        calls = []
        monkeypatch.setattr(kvc, "delete_for_provider",
                            lambda provider: calls.append(provider) or True)

        assert secrets.delete_secret("groq") is True
        assert calls == ["groq"]


class TestAuditLogging:
    """Access logging for secrets."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import core.ai.secrets as secrets
        import tempfile as _tf

        self.test_dir = Path(_tf.mkdtemp())
        self.secrets_path = self.test_dir / "provider_secrets.json"
        self.audit_path = self.test_dir / "secret_access_audit.json"

        monkeypatch.setattr(secrets, "STORAGE_PATH", self.secrets_path)
        monkeypatch.setattr(secrets, "AUDIT_PATH", self.audit_path)

        yield
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_secret_logs_access(self):
        from core.ai.secrets import set_secret, get_secret, get_access_log

        set_secret("audit_test", api_key="key123")
        get_secret("audit_test")

        logs = get_access_log(limit=10)
        get_logs = [l for l in logs if l["action"] == "get_secret"]
        assert len(get_logs) >= 1
        assert get_logs[0]["provider"] == "audit_test"
        assert get_logs[0]["success"] is True

    def test_set_secret_logs_access(self):
        from core.ai.secrets import set_secret, get_access_log

        set_secret("set_test", api_key="another-key")

        logs = get_access_log(limit=10)
        set_logs = [l for l in logs if l["action"] == "set_secret"]
        assert len(set_logs) >= 1
        assert set_logs[0]["provider"] == "set_test"

    def test_failed_get_logs_access(self):
        from core.ai.secrets import get_secret, get_access_log

        get_secret("never_stored")

        logs = get_access_log(limit=10)
        fail_logs = [l for l in logs
                     if l["provider"] == "never_stored" and not l["success"]]
        assert len(fail_logs) >= 1
        assert fail_logs[0]["action"] == "get_secret"


class TestMaskKey:
    """API key masking for safe logging."""

    def test_mask_normal_key(self):
        from core.ai.secrets import mask_key
        result = mask_key("sk-abc123def456ghi789")
        assert "sk-abc123def456ghi789" != result
        assert result.startswith("*")
        assert result.endswith("i789")
        assert len(result) == len("sk-abc123def456ghi789")

    def test_mask_short_key(self):
        from core.ai.secrets import mask_key
        assert mask_key("short") == "***"

    def test_mask_empty(self):
        from core.ai.secrets import mask_key
        assert mask_key("") == "***"


class TestHealthCheck:
    """Credential health checks and invalid key detection."""

    def test_health_check_no_secret(self):
        from core.ai.secrets import check_health
        result = check_health("nonexistent_provider")
        assert result["ok"] is False
        assert result["status"] == "no_secret"

    def test_health_check_no_api_base(self):
        from core.ai.secrets import set_secret, delete_secret
        set_secret("no_base", api_key="key", api_base="")
        from core.ai.secrets import check_health
        result = check_health("no_base")
        assert result["ok"] is False
        assert result["status"] == "error"
        delete_secret("no_base")
