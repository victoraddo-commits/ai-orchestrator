"""Phase 17U: Provider config editor tests — operator-set default workers and fallback order."""

import pytest

import core.provider_config_editor as pce
from core.memory import load


class TestLoadSaveDefaults:
    def test_load_overrides_returns_default_on_no_file(self, isolated_memory):
        result = pce.load_overrides()
        assert result == {"schema_version": 1, "overrides": {}}

    def test_load_overrides_persists_and_reloads(self, isolated_memory):
        overrides = {"fallback_order": {"coding": ["qwen3_coding", "claude"]}}
        success, errors, warnings = pce.save_overrides(overrides)
        assert success is True
        assert errors == []

        result = pce.load_overrides()
        assert result["schema_version"] == 1
        assert result["overrides"] == overrides

    def test_get_fallback_order_returns_none_when_no_override(self, isolated_memory):
        assert pce.get_fallback_order("coding") is None
        assert pce.get_fallback_order("planning") is None
        assert pce.get_fallback_order("nonexistent_role") is None

    def test_get_fallback_order_returns_override_when_set(self, isolated_memory):
        pce.save_overrides({"fallback_order": {"coding": ["claude", "qwen3_coding"]}})
        assert pce.get_fallback_order("coding") == ["claude", "qwen3_coding"]
        assert pce.get_fallback_order("planning") is None  # not set

    def test_get_max_concurrent_builds_returns_none_when_not_set(self, isolated_memory):
        assert pce.get_max_concurrent_builds() is None

    def test_get_max_concurrent_builds_returns_value_when_set(self, isolated_memory):
        pce.save_overrides({"max_concurrent_builds": 8})
        assert pce.get_max_concurrent_builds() == 8


class TestValidation:
    def test_rejects_fallback_order_that_is_not_a_dict(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides({"fallback_order": ["not", "a", "dict"]})
        assert valid is False
        assert any("must be a dict" in e for e in errors)

    def test_rejects_fallback_order_list_that_is_not_a_list(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides({"fallback_order": {"coding": "not-a-list"}})
        assert valid is False
        assert any("must be a list" in e for e in errors)

    def test_rejects_empty_fallback_order_list(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides({"fallback_order": {"coding": []}})
        assert valid is False
        assert any("empty" in e for e in errors)

    def test_rejects_unregistered_provider(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"fallback_order": {"coding": ["this_provider_does_not_exist_xyz"]}}
        )
        assert valid is False
        assert any("not registered" in e for e in errors)

    def test_rejects_duplicate_provider_names(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"fallback_order": {"coding": ["claude", "claude"]}}
        )
        assert valid is False
        assert any("duplicate" in e for e in errors)

    def test_accepts_valid_fallback_order(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"fallback_order": {"coding": ["claude", "gemini"]}}
        )
        assert valid is True
        assert errors == []

    def test_warns_when_all_providers_unavailable(self, isolated_memory, monkeypatch):
        import core.ai_provider as ai_provider

        monkeypatch.setattr(ai_provider, "_PROVIDERS", {
            "claude": {"available_fn": lambda: False, "capabilities": ["coding_agent", "text_task", "file_access"], "kind": "cloud", "description": "test claude", "cost_tier": "paid", "enabled": True},
            "gemini": {"available_fn": lambda: False, "capabilities": ["text_task"], "kind": "cloud", "description": "test gemini", "cost_tier": "free", "enabled": True},
        })

        valid, errors, warnings = pce.validate_overrides(
            {"fallback_order": {"coding": ["claude", "gemini"]}}
        )
        assert valid is True
        assert any("unavailable" in w.lower() for w in warnings)

    def test_accepts_overrides_with_no_fallback_order(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"max_concurrent_builds": 4}
        )
        assert valid is True

    def test_rejects_max_concurrent_builds_less_than_one(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"max_concurrent_builds": 0}
        )
        assert valid is False
        assert any(">= 1" in e for e in errors)

    def test_rejects_max_concurrent_builds_not_an_int(self, isolated_memory):
        valid, errors, warnings = pce.validate_overrides(
            {"max_concurrent_builds": "many"}
        )
        assert valid is False
        assert any("integer" in e.lower() for e in errors)


class TestSaveOverrides:
    def test_save_overrides_writes_to_memory_file(self, isolated_memory):
        success, errors, warnings = pce.save_overrides(
            {"fallback_order": {"coding": ["claude"]}}
        )
        assert success is True
        assert errors == []

        data = load(pce.OVERRIDES_FILE)
        assert data["schema_version"] == 1
        assert data["overrides"]["fallback_order"]["coding"] == ["claude"]

    def test_save_overrides_returns_errors_on_invalid_input(self, isolated_memory):
        success, errors, warnings = pce.save_overrides(
            {"fallback_order": {"coding": ["nonexistent_provider_xxx"]}}
        )
        assert success is False
        assert len(errors) > 0

    def test_save_overrides_merges_with_existing(self, isolated_memory):
        pce.save_overrides({"fallback_order": {"coding": ["claude"]}})
        pce.save_overrides({
            "fallback_order": {"planning": ["gemini"]},
        })

        result = pce.load_overrides()
        assert result["overrides"]["fallback_order"]["coding"] == ["claude"]
        assert result["overrides"]["fallback_order"]["planning"] == ["gemini"]

    def test_save_overrides_with_multiple_roles(self, isolated_memory):
        pce.save_overrides({
            "fallback_order": {
                "coding": ["claude", "qwen3_coding"],
                "planning": ["gemini", "deepseek_native_flash"],
                "review": ["openai"],
            },
            "max_concurrent_builds": 8,
        })

        result = pce.load_overrides()
        assert result["overrides"]["fallback_order"]["coding"] == ["claude", "qwen3_coding"]
        assert result["overrides"]["fallback_order"]["planning"] == ["gemini", "deepseek_native_flash"]
        assert result["overrides"]["fallback_order"]["review"] == ["openai"]
        assert result["overrides"]["max_concurrent_builds"] == 8


class TestGetFullConfig:
    def test_returns_schema_version_and_overrides(self, isolated_memory):
        pce.save_overrides({"fallback_order": {"coding": ["claude"]}})
        config = pce.get_full_config()

        assert config["schema_version"] == 1
        assert config["overrides"]["fallback_order"]["coding"] == ["claude"]
        assert "validation" in config
        assert config["validation"]["valid"] is True

    def test_validation_errors_reported_in_full_config(self, isolated_memory):
        # Write an invalid override directly to the file (bypassing save)
        from core.memory import save
        save(pce.OVERRIDES_FILE, {
            "schema_version": 1,
            "overrides": {"fallback_order": {"coding": ["nonexistent_xx"]}},
        })

        config = pce.get_full_config()
        assert config["validation"]["valid"] is False
        assert len(config["validation"]["errors"]) > 0

    def test_empty_config_has_no_errors(self, isolated_memory):
        config = pce.get_full_config()
        assert config["schema_version"] == 1
        assert config["overrides"] == {}
        assert config["validation"]["valid"] is True
        assert config["validation"]["errors"] == []


class TestBackwardCompatibility:
    def test_get_fallback_order_returns_none_with_empty_overrides(self, isolated_memory):
        """Legacy behavior: no overrides means ROLE_PROVIDERS is used unchanged."""
        assert pce.get_fallback_order("coding") is None

    def test_get_max_concurrent_builds_returns_none_with_empty_overrides(self, isolated_memory):
        """Legacy build manager uses its own default when no override is set."""
        assert pce.get_max_concurrent_builds() is None

    def test_partial_overrides_dont_affect_unconfigured_roles(self, isolated_memory):
        """Setting fallback_order for 'coding' should not affect 'planning'."""
        pce.save_overrides({"fallback_order": {"coding": ["claude"]}})
        assert pce.get_fallback_order("coding") == ["claude"]
        assert pce.get_fallback_order("planning") is None


class TestRouterIntegration:
    """Tests that _candidates_for respects the override layer."""

    def test_router_uses_override_when_set(self, isolated_memory, monkeypatch):
        from core.ai import ai_router

        pce.save_overrides({"fallback_order": {"planning": ["claude", "gemini"]}})

        # Mock rotation to be deterministic
        monkeypatch.setattr(ai_router, "_rotate_candidates", lambda task_type, candidates: candidates)

        candidates = ai_router._candidates_for("planning")
        assert candidates == ["claude", "gemini"]

    def test_router_falls_back_to_default_when_no_override(self, isolated_memory):
        from core.ai import ai_router

        # Ensure no overrides
        assert pce.get_fallback_order("planning") is None

        candidates = ai_router._candidates_for("planning")
        # Should use the hardcoded ROLE_PROVIDERS default
        assert isinstance(candidates, list)
        assert len(candidates) > 0

    def test_router_uses_override_for_coding_with_rotation(self, isolated_memory, monkeypatch):
        from core.ai import ai_router

        pce.save_overrides({"fallback_order": {"coding": ["qwen3_coding", "opencode_claude", "claude"]}})

        # Make rotation deterministic
        monkeypatch.setattr(ai_router, "_rotate_candidates", lambda task_type, candidates: candidates)

        candidates = ai_router._candidates_for("coding")
        # The rotating front comes first (CODING_ROTATING_FRONT members in the override),
        # then the fixed tail
        assert "qwen3_coding" in candidates
        assert "claude" in candidates

    def test_router_for_nonexistent_role_without_override(self, isolated_memory):
        from core.ai import ai_router

        assert pce.get_fallback_order("nonexistent_role") is None
        candidates = ai_router._candidates_for("nonexistent_role")
        assert candidates == ["claude"]


class TestGetFallbackOrderEdgeCases:
    def test_empty_list_is_treated_as_none(self, isolated_memory):
        """An empty list stored in the file (shouldn't happen via save, but guard anyway)."""
        from core.memory import save
        save(pce.OVERRIDES_FILE, {
            "schema_version": 1,
            "overrides": {"fallback_order": {"coding": []}},
        })
        result = pce.get_fallback_order("coding")
        assert result is None

    def test_non_list_value_is_treated_as_none(self, isolated_memory):
        from core.memory import save
        save(pce.OVERRIDES_FILE, {
            "schema_version": 1,
            "overrides": {"fallback_order": {"coding": "not-a-list"}},
        })
        result = pce.get_fallback_order("coding")
        assert result is None


class TestAPIEndpoints:
    """Integration tests for GET/PUT/DELETE /providers/config endpoints."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from core.api import app
        return TestClient(app)

    @pytest.fixture
    def auth_headers(self):
        import core.api as api_module
        return {"Authorization": f"Bearer {api_module._load_api_token()}"}

    def test_get_config_returns_default_when_no_overrides(self, isolated_memory, client):
        response = client.get("/providers/config")
        assert response.status_code == 200
        data = response.json()
        assert data["schema_version"] == 1
        assert data["overrides"] == {}
        assert data["validation"]["valid"] is True

    def test_put_config_sets_fallback_order(self, isolated_memory, client, auth_headers):
        body = {
            "fallback_order": {
                "coding": ["claude", "qwen3_coding"],
                "planning": ["gemini", "deepseek_native_flash"],
            },
        }
        response = client.put("/providers/config", json=body, headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["saved"] is True
        assert data["overrides"]["fallback_order"]["coding"] == ["claude", "qwen3_coding"]
        assert data["overrides"]["fallback_order"]["planning"] == ["gemini", "deepseek_native_flash"]

    def test_put_config_sets_max_concurrent_builds(self, isolated_memory, client, auth_headers):
        body = {"max_concurrent_builds": 6}
        response = client.put("/providers/config", json=body, headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["saved"] is True
        assert data["overrides"]["max_concurrent_builds"] == 6

    def test_put_config_rejects_unregistered_provider(self, isolated_memory, client, auth_headers):
        body = {
            "fallback_order": {
                "coding": ["nonexistent_provider_zzz"],
            },
        }
        response = client.put("/providers/config", json=body, headers=auth_headers)
        assert response.status_code == 422
        assert "errors" in response.json()["detail"]

    def test_put_config_requires_auth(self, isolated_memory, client):
        response = client.put("/providers/config", json={"max_concurrent_builds": 8})
        assert response.status_code == 401

    def test_delete_config_resets_overrides(self, isolated_memory, client, auth_headers):
        # First set an override
        client.put("/providers/config", json={"fallback_order": {"coding": ["claude"]}}, headers=auth_headers)

        # Verify it's set
        get_resp = client.get("/providers/config")
        assert get_resp.json()["overrides"]["fallback_order"]["coding"] == ["claude"]

        # Delete
        response = client.delete("/providers/config", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["reset"] is True

        # Verify it's gone
        get_resp2 = client.get("/providers/config")
        assert get_resp2.json()["overrides"] == {}

    def test_delete_config_requires_auth(self, isolated_memory, client):
        response = client.delete("/providers/config")
        assert response.status_code == 401

    def test_put_config_merges_with_existing(self, isolated_memory, client, auth_headers):
        client.put("/providers/config", json={"fallback_order": {"coding": ["claude"]}}, headers=auth_headers)
        client.put("/providers/config", json={"fallback_order": {"planning": ["gemini"]}}, headers=auth_headers)

        get_resp = client.get("/providers/config")
        overrides = get_resp.json()["overrides"]["fallback_order"]
        assert overrides["coding"] == ["claude"]
        assert overrides["planning"] == ["gemini"]

    def test_put_config_rejects_empty_fallback_list(self, isolated_memory, client, auth_headers):
        body = {"fallback_order": {"coding": []}}
        response = client.put("/providers/config", json=body, headers=auth_headers)
        assert response.status_code == 422

    def test_get_config_after_put_shows_validation(self, isolated_memory, client, auth_headers):
        client.put("/providers/config", json={"fallback_order": {"coding": ["claude", "gemini"]}}, headers=auth_headers)

        response = client.get("/providers/config")
        assert response.status_code == 200
        data = response.json()
        assert data["validation"]["valid"] is True
        assert data["validation"]["errors"] == []
