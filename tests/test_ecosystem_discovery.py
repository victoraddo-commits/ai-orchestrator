import pytest, tempfile, os, json
from pathlib import Path

_test_dir = tempfile.mkdtemp()
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = _test_dir

from core.ecosystem_discovery import (
    scan_src_directory, find_secret_stores, find_telegram_bots,
    find_notification_systems, find_docker_services,
    build_initial_graph,
)

def test_scan_src_directory_finds_modules():
    results = scan_src_directory(Path("/project/src"))
    ids = [r["id"] for r in results]
    assert "kai-vault" in ids
    assert "kai-notify" in ids

def test_find_secret_stores_detects_json_secrets():
    stores = find_secret_stores(Path("/project/ai-orchestrator/core/ai/secrets.py"))
    assert len(stores) >= 1
    assert any("secrets.py" in s["id"] or "provider_secrets" in s["id"] for s in stores)

def test_find_telegram_bots_detects_notify():
    bots = find_telegram_bots(Path("/project/src/kai-notify/src/index.js"))
    assert len(bots) >= 1
    assert bots[0]["platform"] == "telegram"

def test_find_notification_systems():
    systems = find_notification_systems(Path("/project/src"))
    ids = [s["id"] for s in systems]
    assert "kai-notify" in ids

def test_build_initial_graph_has_required_keys():
    graph = build_initial_graph()
    assert "entities" in graph
    assert "capabilities" in graph
    assert "relationships" in graph
    assert "last_updated" in graph

def test_build_initial_graph_detects_vault():
    graph = build_initial_graph()
    assert "kai-vault" in graph["entities"]
    vault = graph["entities"]["kai-vault"]
    assert vault["type"] == "capability_owner"
    assert vault["canonical_owner"] is True
