"""Ecosystem discovery tests — host-independent.

The discovery scanner must work anywhere. Historically these tests hardcoded
``/project/src`` (the claude-code master-repo layout), so they failed on the
runner (CT111) where ``/project`` is only a symlink and ``/project/src`` does
not exist. Every test here builds its own fixture tree under ``tmp_path`` and
passes it in explicitly; the default path is exercised only to prove graceful
degradation when it is absent.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Keep any accidental memory writes out of the real store.
os.environ.setdefault("AI_ORCHESTRATOR_MEMORY_DIR", tempfile.mkdtemp())

from core.ecosystem_discovery import (
    scan_src_directory, find_secret_stores, find_telegram_bots,
    find_notification_systems, find_docker_services,
    build_initial_graph,
)


@pytest.fixture
def fake_src(tmp_path):
    """A temporary src root with fixture modules (no /project dependency)."""
    src = tmp_path / "src"

    vault = src / "kai-vault"
    vault.mkdir(parents=True)
    (vault / "README.md").write_text("# Kai Vault\nSecret management service\n")

    notify = src / "kai-notify" / "src"
    notify.mkdir(parents=True)
    (notify / "index.js").write_text(
        "const telegram = require('telegram');\n"
        "async function sendMessage(chat, text) { /* notification hub */ }\n"
        "const client = new TelegraClient();\n"
    )
    return src


def test_scan_src_directory_finds_modules(fake_src):
    results = scan_src_directory(fake_src)
    ids = [r["id"] for r in results]
    assert "kai-vault" in ids
    assert "kai-notify" in ids

    vault = next(r for r in results if r["id"] == "kai-vault")
    assert vault["type"] == "capability_owner"
    assert vault["canonical_owner"] is True


def test_scan_src_directory_missing_root_returns_empty():
    # Graceful degradation: a host without /project/src must not crash.
    assert scan_src_directory(Path("/nonexistent")) == []


def test_find_secret_stores_detects_json_secrets(tmp_path):
    secrets_py = tmp_path / "secrets.py"
    secrets_py.write_text(
        "STORAGE_PATH = 'provider_secrets.json'\n"
        "api_key = 'secret'\n"
        "password = 'x'\n"
        "token = 'y'\n"
    )

    stores = find_secret_stores(secrets_py)
    assert len(stores) >= 1
    assert any("secrets.py" in s["id"] or "provider_secrets" in s["id"]
               for s in stores)


def test_find_telegram_bots_detects_notify(fake_src):
    bots = find_telegram_bots(fake_src / "kai-notify" / "src" / "index.js")
    assert len(bots) >= 1
    assert bots[0]["platform"] == "telegram"


def test_find_notification_systems(fake_src):
    systems = find_notification_systems(fake_src)
    ids = [s["id"] for s in systems]
    assert "kai-notify" in ids


def test_find_notification_systems_missing_root_returns_empty():
    assert find_notification_systems(Path("/nonexistent")) == []


def test_build_initial_graph_has_required_keys(fake_src):
    graph = build_initial_graph(root=fake_src)
    assert "entities" in graph
    assert "capabilities" in graph
    assert "relationships" in graph
    assert "last_updated" in graph


def test_build_initial_graph_tolerates_missing_root():
    # No root -> empty scan collections, but the graph contract still holds.
    graph = build_initial_graph(root=Path("/nonexistent"))
    assert isinstance(graph["entities"], dict)
    assert "ai-orchestrator" in graph["entities"]  # static orchestrator entity
    assert isinstance(graph["capabilities"], dict) and graph["capabilities"]
    assert graph["relationships"] == []
    assert graph["last_updated"]


def test_build_initial_graph_detects_vault(fake_src):
    graph = build_initial_graph(root=fake_src)
    assert "kai-vault" in graph["entities"]
    vault = graph["entities"]["kai-vault"]
    assert vault["type"] == "capability_owner"
    assert vault["canonical_owner"] is True
