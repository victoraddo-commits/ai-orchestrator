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
