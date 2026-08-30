import pytest, tempfile, json, os
from pathlib import Path

# Set test memory dir before importing anything
_test_dir = tempfile.mkdtemp()
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = _test_dir

from core.ecosystem_graph import (
    load_graph, save_graph, update_graph,
    add_entity, update_entity, get_entity, list_entities,
    add_capability, get_capability,
    add_relationship, get_relationships,
    detect_changes, get_blast_radius,
    export_to_yaml,
)

def test_load_graph_returns_empty_when_no_file():
    graph = load_graph()
    # schema_version is always present (consistent with network_knowledge.py pattern)
    assert graph["entities"] == {}
    assert graph["capabilities"] == {}
    assert graph["relationships"] == []
    assert graph["last_updated"] is None
    assert graph["schema_version"] == 1

def test_save_and_load_round_trip():
    graph = {
        "entities": {"vault": {"id": "vault", "type": "capability_owner", "name": "KAI Vault", "status": "active"}},
        "capabilities": {},
        "relationships": [],
        "last_updated": "2026-08-30T00:00:00Z",
    }
    save_graph(graph)
    loaded = load_graph()
    assert loaded["entities"]["vault"]["id"] == "vault"
    assert loaded["entities"]["vault"]["name"] == "KAI Vault"

def test_add_entity():
    entity = {"id": "kai-notify", "type": "service", "name": "KAI Notify", "status": "active", "canonical_owner": False}
    add_entity(entity)
    assert get_entity("kai-notify")["name"] == "KAI Notify"

def test_add_capability():
    cap = {"id": "notification", "name": "Notification", "canonical_owner": "kai-notify", "status": "active"}
    add_capability(cap)
    assert get_capability("notification")["canonical_owner"] == "kai-notify"

def test_add_relationship():
    rel = {"from": "ai-orchestrator", "to": "vault", "type": "reads_from", "description": "Reads secrets"}
    add_relationship(rel)
    rels = get_relationships(from_entity="ai-orchestrator")
    assert any(r["to"] == "vault" for r in rels)

def test_detect_changes_detects_new_entity():
    old = {"entities": {}, "capabilities": {}, "relationships": [], "last_updated": "2026-08-30T00:00:00Z"}
    new_entities = {"vault": {"id": "vault", "type": "capability_owner", "name": "KAI Vault", "status": "active"}}
    changes = detect_changes(old, {"entities": new_entities, "capabilities": {}, "relationships": []})
    assert "added" in changes
    assert "vault" in changes["added"]["entities"]

def test_get_blast_radius_high_for_widely_drained_entity():
    graph = {
        "entities": {"vault": {"id": "vault", "status": "active"}},
        "capabilities": {},
        "relationships": [
            {"from": "ai-orchestrator", "to": "vault", "type": "reads_from"},
            {"from": "kai-money", "to": "vault", "type": "reads_from"},
            {"from": "kai-notify", "to": "vault", "type": "reads_from"},
            {"from": "kai-audit", "to": "vault", "type": "reads_from"},
            {"from": "talent", "to": "vault", "type": "reads_from"},
            {"from": "it-manager", "to": "vault", "type": "reads_from"},
        ],
        "last_updated": None,
    }
    save_graph(graph)
    br = get_blast_radius("vault")
    assert br["level"] == "high"
    assert br["count"] == 6

def test_get_blast_radius_low_for_isolated_entity():
    graph = {
        "entities": {"kai-voice-hud": {"id": "kai-voice-hud", "status": "active"}},
        "capabilities": {},
        "relationships": [{"from": "proxdash", "to": "kai-voice-hud", "type": "calls"}],
        "last_updated": None,
    }
    save_graph(graph)
    br = get_blast_radius("kai-voice-hud")
    assert br["level"] == "low"
    assert br["count"] == 1

def test_update_entity_returns_false_for_nonexistent_entity():
    graph = {
        "entities": {"vault": {"id": "vault", "type": "capability_owner", "status": "active"}},
        "capabilities": {},
        "relationships": [],
        "last_updated": None,
    }
    save_graph(graph)
    result = update_entity("does-not-exist", {"status": "inactive"})
    assert result is False
    # Original entity unchanged
    assert get_entity("vault")["status"] == "active"

def test_update_entity_returns_true_for_existing_entity():
    graph = {
        "entities": {"vault": {"id": "vault", "type": "capability_owner", "status": "active"}},
        "capabilities": {},
        "relationships": [],
        "last_updated": None,
    }
    save_graph(graph)
    result = update_entity("vault", {"status": "inactive"})
    assert result is True
    assert get_entity("vault")["status"] == "inactive"

def test_list_entities_returns_all_entities():
    graph = {
        "entities": {
            "vault": {"id": "vault", "type": "capability_owner", "status": "active"},
            "talent": {"id": "talent", "type": "service", "status": "inactive"},
        },
        "capabilities": {},
        "relationships": [],
        "last_updated": None,
    }
    save_graph(graph)
    all_ents = list_entities()
    assert len(all_ents) == 2
    filtered = list_entities(status="active")
    assert len(filtered) == 1
    assert filtered[0]["id"] == "vault"

def test_list_capabilities_returns_all_capabilities():
    graph = {
        "entities": {},
        "capabilities": {
            "notification": {"id": "notification", "status": "active"},
            "storage": {"id": "storage", "status": "inactive"},
        },
        "relationships": [],
        "last_updated": None,
    }
    save_graph(graph)
    from core.ecosystem_graph import list_capabilities
    all_caps = list_capabilities()
    assert len(all_caps) == 2
    filtered = list_capabilities(status="active")
    assert len(filtered) == 1
    assert filtered[0]["id"] == "notification"

def test_get_relationships_with_filters():
    graph = {
        "entities": {},
        "capabilities": {},
        "relationships": [
            {"from": "ai-orchestrator", "to": "vault", "type": "reads_from"},
            {"from": "kai-money", "to": "vault", "type": "reads_from"},
            {"from": "ai-orchestrator", "to": "talent", "type": "calls"},
        ],
        "last_updated": None,
    }
    save_graph(graph)
    all_from_orch = get_relationships(from_entity="ai-orchestrator")
    assert len(all_from_orch) == 2
    only_vault = get_relationships(to_entity="vault")
    assert len(only_vault) == 2
    only_calls = get_relationships(rel_type="calls")
    assert len(only_calls) == 1
    assert only_calls[0]["from"] == "ai-orchestrator"

def test_detect_changes_detects_changed_relationship():
    """Same from/to/type but different description should be detected as changed."""
    old = {
        "entities": {},
        "capabilities": {},
        "relationships": [
            {"from": "ai-orchestrator", "to": "vault", "type": "reads_from", "description": "Reads secrets"}
        ],
        "last_updated": None,
    }
    new = {
        "entities": {},
        "capabilities": {},
        "relationships": [
            {"from": "ai-orchestrator", "to": "vault", "type": "reads_from", "description": "Reads secrets and config"}
        ],
        "last_updated": None,
    }
    changes = detect_changes(old, new)
    assert "changed" in changes
    assert len(changes["changed"]["relationships"]) == 1
    assert changes["changed"]["relationships"][0]["description"] == "Reads secrets and config"

def test_add_entity_raises_when_id_missing():
    from core.ecosystem_graph import add_entity
    with pytest.raises(ValueError, match="entity must have an 'id' field"):
        add_entity({"type": "service", "name": "No ID"})

def test_add_capability_raises_when_id_missing():
    from core.ecosystem_graph import add_capability
    with pytest.raises(ValueError, match="entity must have an 'id' field"):
        add_capability({"name": "No ID", "status": "active"})

def test_get_blast_radius_returns_none_for_zero_relationships():
    graph = {
        "entities": {"orphan": {"id": "orphan", "status": "active"}},
        "capabilities": {},
        "relationships": [],
        "last_updated": None,
    }
    save_graph(graph)
    br = get_blast_radius("orphan")
    assert br["level"] == "none"
    assert br["count"] == 0
