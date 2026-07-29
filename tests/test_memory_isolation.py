import json
from pathlib import Path

import pytest

import core.memory as memory


PRODUCTION_INCIDENTS = Path("memory/incidents.json")


def test_save_blocks_writes_to_production_dir_during_tests():
    with pytest.raises(memory.ProductionMemoryWriteBlocked):
        memory.save(
            "incidents.json",
            [{"service": "test-service", "issue": "should never land"}],
            directory=memory.PRODUCTION_MEMORY_DIR,
        )


def test_default_save_call_never_touches_real_production_file():
    before = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    memory.save("incidents.json", [{"service": "test-service", "issue": "fake"}])

    after = PRODUCTION_INCIDENTS.read_bytes() if PRODUCTION_INCIDENTS.exists() else None

    assert before == after


def test_default_save_and_load_round_trip_in_isolated_dir(isolated_memory):
    memory.save("incidents.json", [{"service": "x"}])

    assert memory.load("incidents.json") == [{"service": "x"}]
    assert (isolated_memory / "incidents.json").exists()
    assert not (Path("memory") / "incidents.json.does-not-exist-marker").exists()


def test_save_persists_with_schema_version_envelope_on_disk(isolated_memory):
    import json

    memory.save("incidents.json", [{"service": "x"}])

    raw = json.loads((isolated_memory / "incidents.json").read_text())
    assert raw["schema_version"] == 1
    assert raw["records"] == [{"service": "x"}]


def test_update_blocks_writes_to_production_dir_during_tests():
    with pytest.raises(memory.ProductionMemoryWriteBlocked):
        memory.update(
            "incidents.json",
            lambda records: records,
            directory=memory.PRODUCTION_MEMORY_DIR,
        )


def test_update_round_trips_in_isolated_dir(isolated_memory):
    memory.save("counters.json", {"count": 1})

    result = memory.update("counters.json", lambda state: {**state, "count": state["count"] + 1})

    assert result == {"count": 2}
    assert memory.load("counters.json") == {"count": 2}


def test_update_defaults_to_empty_dict_when_file_missing(isolated_memory):
    result = memory.update("brand_new.json", lambda state: {**state, "seeded": True})

    assert result == {"seeded": True}
    assert memory.load("brand_new.json") == {"seeded": True}


def test_load_recovers_from_corrupted_file_via_backup(isolated_memory):
    memory.save("incidents.json", [{"service": "a"}])
    memory.save("incidents.json", [{"service": "a"}, {"service": "b"}])

    (isolated_memory / "incidents.json").write_text("{not valid json")

    assert memory.load("incidents.json") == [{"service": "a"}]
