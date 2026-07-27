import json
from pathlib import Path

from core.test_memory import create_test_incident, TEST_MEMORY_DIR


PRODUCTION_INCIDENTS = Path("memory/incidents.json")


def test_create_test_incident_writes_only_to_isolated_test_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.test_memory.TEST_MEMORY_DIR",
        tmp_path,
    )

    before = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    incident = create_test_incident("critical", "test-service", "Simulated outage")

    after = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    assert before == after
    assert json.loads((tmp_path / "incidents.json").read_text())["records"] == [incident]


def test_create_test_incident_returns_normalized_incident_shape():
    incident = create_test_incident("warning", "svc", "issue text")

    assert set(incident) == {"id", "severity", "service", "issue", "created", "status"}
    assert len(incident["id"]) == 8
    assert incident["status"] == "open"


def test_simulate_never_writes_to_production_incidents():
    from core.test_failure import simulate

    before = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    simulate()

    after = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    assert before == after
