# ai-orchestrator/tests/test_capability_registry_core.py
"""Tests for core/capability_registry.py."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.capability_registry import CapabilityRegistry


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def fresh_reg(tmp_path):
    """Each test gets a fresh CapabilityRegistry instance.

    Direct construction (same pattern as ServiceRegistry tests) — no need to
    manage _instance because __init__ no longer touches it.
    """
    return CapabilityRegistry(memory_dir=tmp_path)


# ------------------------------------------------------------------
# Save / Load roundtrip
# ------------------------------------------------------------------

def test_save_load_roundtrip(fresh_reg, tmp_path):
    fresh_reg.upsert_capability("cap-a", {
        "name": "Capability A",
        "canonical_owner": "team-a",
        "priority": "P1",
        "status": "healthy",
        "description": "Test capability",
    })
    fresh_reg.save()

    # New instance backed by the same directory must see persisted data
    CapabilityRegistry._instance = None
    reg2 = CapabilityRegistry(memory_dir=tmp_path)

    caps = reg2.list_capabilities()
    assert "cap-a" in caps
    assert caps["cap-a"]["name"] == "Capability A"
    assert caps["cap-a"]["canonical_owner"] == "team-a"
    assert caps["cap-a"]["priority"] == "P1"
    assert caps["cap-a"]["status"] == "healthy"


def test_atomic_save_creates_backup(fresh_reg, tmp_path):
    fresh_reg.upsert_capability("cap-x", {
        "name": "Original",
        "canonical_owner": "o",
        "priority": "P0",
        "status": "healthy",
    })
    fresh_reg.save()

    fresh_reg.upsert_capability("cap-x", {
        "name": "Updated",
        "canonical_owner": "o",
        "priority": "P0",
        "status": "degraded",
    })
    fresh_reg.save()

    bak = tmp_path / "kai_capabilities.json.bak"
    assert bak.exists()
    with open(bak) as f:
        bak_data = json.load(f)
    assert bak_data["cap-x"]["name"] == "Original"


# ------------------------------------------------------------------
# list_capabilities filters
# ------------------------------------------------------------------

def test_list_capabilities_no_filter(fresh_reg):
    fresh_reg.upsert_capability("c1", {
        "name": "C1", "canonical_owner": "team", "priority": "P0", "status": "healthy"
    })
    fresh_reg.upsert_capability("c2", {
        "name": "C2", "canonical_owner": "team", "priority": "P1", "status": "degraded"
    })
    fresh_reg.upsert_capability("c3", {
        "name": "C3", "canonical_owner": "other", "priority": "P2", "status": "down"
    })

    all_caps = fresh_reg.list_capabilities()
    assert len(all_caps) == 3

    healthy = fresh_reg.list_capabilities(status="healthy")
    assert list(healthy.keys()) == ["c1"]

    p0 = fresh_reg.list_capabilities(priority="P0")
    assert list(p0.keys()) == ["c1"]

    team_caps = fresh_reg.list_capabilities(owner="team")
    assert set(team_caps.keys()) == {"c1", "c2"}

    combo = fresh_reg.list_capabilities(status="degraded", priority="P1")
    assert list(combo.keys()) == ["c2"]


def test_list_capabilities_status_filter_no_match(fresh_reg):
    fresh_reg.upsert_capability("c1", {
        "name": "C1", "canonical_owner": "o", "priority": "P0", "status": "healthy"
    })
    assert fresh_reg.list_capabilities(status="degraded") == {}


# ------------------------------------------------------------------
# get_capability / upsert_capability / delete_capability
# ------------------------------------------------------------------

def test_get_capability_exists(fresh_reg):
    fresh_reg.upsert_capability("my-cap", {
        "name": "Mine", "canonical_owner": "owner", "priority": "P1", "status": "healthy"
    })
    cap = fresh_reg.get_capability("my-cap")
    assert cap is not None
    assert cap["name"] == "Mine"
    assert cap["capability_id"] == "my-cap"


def test_get_capability_missing(fresh_reg):
    assert fresh_reg.get_capability("does-not-exist") is None


def test_upsert_capability_creates_new(fresh_reg):
    fresh_reg.upsert_capability("new-cap", {
        "name": "New", "canonical_owner": "owner", "priority": "P2", "status": "unknown"
    })
    assert "new-cap" in fresh_reg.list_capabilities()


def test_upsert_capability_updates_existing(fresh_reg):
    fresh_reg.upsert_capability("existing", {
        "name": "Original", "canonical_owner": "o", "priority": "P1", "status": "healthy"
    })
    fresh_reg.upsert_capability("existing", {
        "name": "Updated", "canonical_owner": "o", "priority": "P0", "status": "degraded"
    })
    cap = fresh_reg.get_capability("existing")
    assert cap["name"] == "Updated"
    assert cap["priority"] == "P0"
    assert cap["status"] == "degraded"
    assert cap["implementations"] == []


def test_delete_capability_exists(fresh_reg):
    fresh_reg.upsert_capability("to-delete", {
        "name": "X", "canonical_owner": "o", "priority": "P0", "status": "healthy"
    })
    assert fresh_reg.delete_capability("to-delete") is True
    assert fresh_reg.get_capability("to-delete") is None


def test_delete_capability_missing(fresh_reg):
    assert fresh_reg.delete_capability("never-existed") is False


# ------------------------------------------------------------------
# Implementation links
# ------------------------------------------------------------------

def test_add_implementation_new_link_primary(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    result = fresh_reg.add_implementation("cap", "svc-1", role="primary")
    assert result is True
    impls = fresh_reg.get_capability("cap")["implementations"]
    assert len(impls) == 1
    assert impls[0]["service_id"] == "svc-1"
    assert impls[0]["role"] == "primary"
    assert impls[0]["health"] == "unknown"


def test_add_implementation_secondary_role(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    result = fresh_reg.add_implementation("cap", "svc-2", role="secondary")
    assert result is True
    impls = fresh_reg.get_capability("cap")["implementations"]
    assert len(impls) == 1
    assert impls[0]["service_id"] == "svc-2"
    assert impls[0]["role"] == "secondary"


def test_add_implementation_duplicate_is_noop(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")
    fresh_reg.add_implementation("cap", "svc-1", role="primary")
    assert len(fresh_reg.get_capability("cap")["implementations"]) == 1


def test_add_implementation_missing_cap_returns_false(fresh_reg):
    assert fresh_reg.add_implementation("non-existent-cap", "svc-1") is False


def test_add_implementation_invalid_role_returns_false(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    assert fresh_reg.add_implementation("cap", "svc-1", role="invalid") is False


def test_remove_implementation_exists(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")
    result = fresh_reg.remove_implementation("cap", "svc-1")
    assert result is True
    assert fresh_reg.get_capability("cap")["implementations"] == []


def test_remove_implementation_missing_cap(fresh_reg):
    assert fresh_reg.remove_implementation("non-existent", "svc-1") is False


def test_remove_implementation_missing_impl(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    assert fresh_reg.remove_implementation("cap", "non-linked-svc") is False


# ------------------------------------------------------------------
# compute_status
# ------------------------------------------------------------------

def test_compute_status_no_implementations(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    assert fresh_reg.compute_status("cap") == "unknown"


def test_compute_status_missing_capability(fresh_reg):
    assert fresh_reg.compute_status("does-not-exist") == "unknown"


def test_compute_status_primary_healthy(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-primary", role="primary")
    cap = fresh_reg.get_capability("cap")
    cap["implementations"][0]["health"] = "healthy"
    assert fresh_reg.compute_status("cap") == "healthy"


def test_compute_status_secondary_healthy_no_primary(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-sec", role="secondary")
    cap = fresh_reg.get_capability("cap")
    cap["implementations"][0]["health"] = "healthy"
    assert fresh_reg.compute_status("cap") == "degraded"


def test_compute_status_primary_healthy_takes_precedence(fresh_reg):
    """PRIMARY healthy → healthy even if SECONDARY is down."""
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-pri", role="primary")
    fresh_reg.add_implementation("cap", "svc-sec", role="secondary")
    cap = fresh_reg.get_capability("cap")
    cap["implementations"][0]["health"] = "healthy"   # primary
    cap["implementations"][1]["health"] = "down"      # secondary
    assert fresh_reg.compute_status("cap") == "healthy"


def test_compute_status_all_down(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")
    cap = fresh_reg.get_capability("cap")
    cap["implementations"][0]["health"] = "down"
    assert fresh_reg.compute_status("cap") == "down"


def test_compute_status_mixed_degraded(fresh_reg):
    """No healthy primary, healthy secondary → degraded."""
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")
    fresh_reg.add_implementation("cap", "svc-2", role="secondary")
    cap = fresh_reg.get_capability("cap")
    cap["implementations"][0]["health"] = "degraded"  # primary not healthy
    cap["implementations"][1]["health"] = "healthy"   # secondary healthy
    assert fresh_reg.compute_status("cap") == "degraded"


# ------------------------------------------------------------------
# refresh_health
# ------------------------------------------------------------------

def test_refresh_health_missing_cap_does_nothing(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.refresh_health("non-existent")  # must not raise


def test_refresh_health_updates_impl_from_service_registry(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "unknown"
    })
    fresh_reg.add_implementation("cap", "svc-healthy", role="primary")
    fresh_reg.add_implementation("cap", "svc-down", role="secondary")

    def get_service(sid):
        return {"svc-healthy": {"status": "healthy"}, "svc-down": {"status": "down"}}.get(sid)

    mock_sr = MagicMock()
    mock_sr.get_service.side_effect = get_service

    with patch("core.service_registry.ServiceRegistry.get_instance", return_value=mock_sr):
        fresh_reg.refresh_health("cap")

    cap = fresh_reg.get_capability("cap")
    impl_health = {i["service_id"]: i["health"] for i in cap["implementations"]}
    assert impl_health["svc-healthy"] == "healthy"
    assert impl_health["svc-down"] == "down"
    assert cap["status"] == "healthy"


def test_refresh_health_records_event_on_status_change(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "healthy"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")

    mock_sr = MagicMock()
    mock_sr.get_service.return_value = {"status": "down"}
    with patch("core.service_registry.ServiceRegistry.get_instance", return_value=mock_sr):
        fresh_reg.refresh_health("cap")

    cap = fresh_reg.get_capability("cap")
    assert cap["status"] == "down"
    assert len(fresh_reg._health_history) == 1
    evt = fresh_reg._health_history[0]
    assert evt["old_status"] == "healthy"
    assert evt["new_status"] == "down"


def test_refresh_health_no_event_when_status_unchanged(fresh_reg):
    fresh_reg.upsert_capability("cap", {
        "name": "Cap", "canonical_owner": "o", "priority": "P1", "status": "down"
    })
    fresh_reg.add_implementation("cap", "svc-1", role="primary")

    mock_sr = MagicMock()
    mock_sr.get_service.return_value = {"status": "down"}
    with patch("core.service_registry.ServiceRegistry.get_instance", return_value=mock_sr):
        fresh_reg.refresh_health("cap")

    assert len(fresh_reg._health_history) == 0


# ------------------------------------------------------------------
# _record_health_event
# ------------------------------------------------------------------

def test_record_health_event_trims_to_max(fresh_reg, monkeypatch):
    monkeypatch.setattr("core.capability_registry.MAX_HEALTH_HISTORY", 5)
    for i in range(10):
        fresh_reg._record_health_event(f"cap-{i % 3}", f"h{i}", f"h{i-1}")
    assert len(fresh_reg._health_history) == 5


def test_record_health_event_noop_on_same_status(fresh_reg):
    fresh_reg._record_health_event("cap", "healthy", "healthy")
    assert len(fresh_reg._health_history) == 0


# ------------------------------------------------------------------
# seed_from_explicit_mapping
# ------------------------------------------------------------------

def test_seed_from_explicit_mapping_creates_missing_caps(fresh_reg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.capability_registry.MAPPING_FILE",
        tmp_path / "mapping.json",
    )
    mapping_data = {
        "svc-1": "cap-from-mapping",
        "svc-2": "existing-cap",
    }
    (tmp_path / "mapping.json").write_text(json.dumps(mapping_data))

    fresh_reg.upsert_capability("existing-cap", {
        "name": "Existing", "canonical_owner": "o", "priority": "P1", "status": "healthy"
    })

    result = fresh_reg.seed_from_explicit_mapping()

    assert result == (1, 2)  # 1 added (cap-from-mapping), 2 changed (svc-1 + svc-2 links)
    cap = fresh_reg.get_capability("cap-from-mapping")
    assert cap is not None
    assert cap["name"] == "cap-from-mapping"


def test_seed_from_explicit_mapping_links_service_as_primary(fresh_reg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.capability_registry.MAPPING_FILE",
        tmp_path / "mapping.json",
    )
    (tmp_path / "mapping.json").write_text(json.dumps({"svc-new": "cap-new"}))
    fresh_reg.seed_from_explicit_mapping()

    impls = fresh_reg.get_capability("cap-new")["implementations"]
    assert any(
        i["service_id"] == "svc-new" and i["role"] == "primary"
        for i in impls
    )


def test_seed_from_explicit_mapping_skips_already_linked(fresh_reg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.capability_registry.MAPPING_FILE",
        tmp_path / "mapping.json",
    )
    (tmp_path / "mapping.json").write_text(json.dumps({"svc-already": "cap-already"}))

    fresh_reg.upsert_capability("cap-already", {
        "name": "C", "canonical_owner": "o", "priority": "P1", "status": "healthy"
    })
    fresh_reg.add_implementation("cap-already", "svc-already", role="primary")

    initial_impls = len(fresh_reg.get_capability("cap-already")["implementations"])
    fresh_reg.seed_from_explicit_mapping()
    final_impls = len(fresh_reg.get_capability("cap-already")["implementations"])

    assert final_impls == initial_impls  # no duplicate


def test_seed_from_explicit_mapping_missing_file_returns_zero(fresh_reg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.capability_registry.MAPPING_FILE",
        tmp_path / "does-not-exist.json",
    )
    assert fresh_reg.seed_from_explicit_mapping() == (0, 0)


# ------------------------------------------------------------------
# Schema defaults
# ------------------------------------------------------------------

def test_schema_defaults_present_on_new_capability(fresh_reg):
    """Newly created capabilities must have all schema default fields."""
    fresh_reg.upsert_capability("brand-new", {
        "name": "N",
        "canonical_owner": "owner",
        "priority": "P2",
        "status": "unknown",
    })
    cap = fresh_reg.get_capability("brand-new")
    assert cap["version"] == "1.0"
    assert cap["description"] == ""
    assert cap["implementations"] == []
    assert cap["permissions_required"] == []
    assert cap["required_identity"] == ""
    assert cap["data_source"] == ""
    assert cap["depends_on"] == []
    assert cap["consumed_by"] == []
    assert cap["consumed_by_override"] == []
    assert cap["health_history"] == []
