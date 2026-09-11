"""Tests for vault_client — .env fallback behavior."""

import os
from unittest.mock import patch

import pytest

from core.vault_client import (
    SECRET_MAP,
    clear_cache,
    get_by_path,
    get_secret,
    is_vault_available,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def test_env_fallback_when_vault_unreachable():
    """When vault is down, secrets come from .env."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-123"}):
        value = get_secret("secrets/ai-providers/gemini", "GEMINI_API_KEY")
    assert value == "test-key-123"


def test_default_when_neither_source_has_value():
    with patch.dict(os.environ, {}, clear=True):
        value = get_secret("secrets/nonexistent", "NONEXISTENT_VAR", default="fallback")
    assert value == "fallback"


def test_returns_none_when_no_default():
    with patch.dict(os.environ, {}, clear=True):
        value = get_secret("secrets/nonexistent", "NONEXISTENT_VAR")
    assert value is None


def test_vault_unavailable():
    assert is_vault_available() is False


def test_get_by_path_uses_env_fallback():
    with patch.dict(os.environ, {"GROQ_API_KEY": "groq-test"}):
        value = get_by_path("secrets/ai-providers/groq")
    assert value == "groq-test"


def test_get_by_path_unknown_path_returns_default():
    value = get_by_path("secrets/unknown/path", default="safe")
    assert value == "safe"


def test_secret_map_covers_all_expected_paths():
    assert len(SECRET_MAP) >= 22
    assert "secrets/ai-providers/gemini" in SECRET_MAP
    assert "secrets/telegram/kai-enzo-bot" in SECRET_MAP
    assert "secrets/infrastructure/proxmox-b" in SECRET_MAP


def test_cache_returns_same_value():
    with patch.dict(os.environ, {"REDIS_PASSWORD": "cached-pw"}):
        v1 = get_secret("secrets/infrastructure/redis", "REDIS_PASSWORD")
        v2 = get_secret("secrets/infrastructure/redis", "REDIS_PASSWORD")
    assert v1 == v2 == "cached-pw"


def test_clear_cache():
    with patch.dict(os.environ, {"REDIS_PASSWORD": "pw1"}):
        get_secret("secrets/infrastructure/redis", "REDIS_PASSWORD")
    clear_cache()
    with patch.dict(os.environ, {"REDIS_PASSWORD": "pw2"}):
        value = get_secret("secrets/infrastructure/redis", "REDIS_PASSWORD")
    assert value == "pw2"
