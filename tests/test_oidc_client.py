"""Tests for core.oidc_client."""
from unittest import mock
import threading

import pytest

from core.oidc_client import OIDCClient, OIDCError


class TestAuthorizationUrl:
    def test_authorization_url_generates_correct_format(self):
        """URL starts with vault /sso/authorize and has client_id, response_type=code."""
        client = OIDCClient()
        url, state = client.get_authorization_url()

        assert url.startswith(f"{client.VAULT_URL}/sso/authorize")
        assert f"client_id={client.CLIENT_ID}" in url
        assert "response_type=code" in url
        # State must be present and non-empty
        assert state
        assert len(state) == 64  # 256-bit hex = 32 bytes = 64 hex chars

    def test_authorization_url_includes_redirect_uri(self):
        """URL contains the encoded redirect_uri."""
        client = OIDCClient()
        url, _ = client.get_authorization_url()

        assert "redirect_uri=" in url
        # redirect_uri value should be URL-encoded
        assert client.CALLBACK_URL in url or client.CALLBACK_URL in url.replace("%3A", ":").replace("%2F", "/")


class TestExchangeCode:
    def test_exchange_code_returns_user_info(self):
        """Mock POST returns user dict with id/username/role + step_up_fresh."""
        client = OIDCClient()
        saved_state = "fake_saved_state"

        with mock.patch.object(client, "_post") as mock_post:
            mock_post.return_value = {
                "user_id": "u123",
                "username": "alice",
                "role": "operator",
                "step_up_fresh": True,
                "issued_at": 1700000000,
            }
            result = client.exchange_code(
                code="auth-code-abc",
                state=saved_state,
                saved_state=saved_state,
            )

        assert result["user"]["id"] == "u123"
        assert result["user"]["username"] == "alice"
        assert result["user"]["role"] == "operator"
        assert result["step_up_fresh"] is True
        assert result["issued_at"] == 1700000000

    def test_exchange_code_invalid_code_raises(self):
        """Mock POST returns {error: 'invalid_grant'}, raises OIDCError."""
        client = OIDCClient()
        saved_state = "fake_saved_state"

        with mock.patch.object(client, "_post") as mock_post:
            mock_post.return_value = {
                "error": "invalid_grant",
                "error_description": "Authorization code expired or invalid.",
            }
            with pytest.raises(OIDCError) as exc_info:
                client.exchange_code(
                    code="bad-code",
                    state=saved_state,
                    saved_state=saved_state,
                )
        assert "invalid_grant" in str(exc_info.value)

    def test_vault_unreachable_raises_descriptive_error(self):
        """Mock raises connection error; OIDCError message contains 'vault_unreachable'."""
        client = OIDCClient()
        saved_state = "fake_saved_state"

        with mock.patch.object(client, "_post") as mock_post:
            mock_post.side_effect = OSError("Connection refused")
            with pytest.raises(OIDCError) as exc_info:
                client.exchange_code(
                    code="any-code",
                    state=saved_state,
                    saved_state=saved_state,
                )
        assert "vault_unreachable" in str(exc_info.value)


class TestRoleMapping:
    def test_role_mapping_defined(self):
        """admin→operator, operator→operator, auditor→viewer, unknown→viewer."""
        assert OIDCClient.map_role("admin") == "operator"
        assert OIDCClient.map_role("operator") == "operator"
        assert OIDCClient.map_role("auditor") == "viewer"
        assert OIDCClient.map_role("unknown") == "viewer"


class TestStateStorage:
    def test_state_storage_is_thread_safe(self):
        """10 concurrent threads get 10 unique states."""
        client = OIDCClient()
        results: list = []
        errors: list = []

        def generate_state():
            try:
                url, state = client.get_authorization_url()
                results.append(state)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=generate_state) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 10
        assert len(set(results)) == 10, "States must be unique"
