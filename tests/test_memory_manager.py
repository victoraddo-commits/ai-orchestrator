import json
import os

import pytest

from core import memory_manager as mm


def test_write_then_read_round_trips_a_list(tmp_path):
    path = tmp_path / "incidents.json"

    mm.write(path, [{"id": "a"}])

    assert mm.read(path, default=[]) == [{"id": "a"}]


def test_write_then_read_round_trips_a_dict(tmp_path):
    path = tmp_path / "system_state.json"

    mm.write(path, {"hostname": "box"})

    assert mm.read(path, default={}) == {"hostname": "box"}


def test_read_missing_file_returns_default(tmp_path):
    path = tmp_path / "does_not_exist.json"

    assert mm.read(path, default=[]) == []
    assert mm.read(path, default={}) == {}


def test_write_wraps_file_on_disk_with_schema_version_envelope(tmp_path):
    path = tmp_path / "incidents.json"

    mm.write(path, [{"id": "a"}])

    raw = json.loads(path.read_text())
    assert raw["schema_version"] == mm.SCHEMA_VERSION
    assert raw["records"] == [{"id": "a"}]


def test_read_transparently_upgrades_legacy_unwrapped_list_file(tmp_path):
    path = tmp_path / "incidents.json"
    path.write_text(json.dumps([{"id": "legacy"}]))

    assert mm.read(path, default=[]) == [{"id": "legacy"}]


def test_read_transparently_upgrades_legacy_unwrapped_dict_file(tmp_path):
    path = tmp_path / "system_state.json"
    path.write_text(json.dumps({"hostname": "legacy-box"}))

    assert mm.read(path, default={}) == {"hostname": "legacy-box"}


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    path = tmp_path / "incidents.json"

    mm.write(path, [{"id": "a"}])

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_write_creates_a_backup_of_the_previous_version(tmp_path):
    path = tmp_path / "incidents.json"

    mm.write(path, [{"id": "a"}])
    mm.write(path, [{"id": "a"}, {"id": "b"}])

    backup = path.with_suffix(path.suffix + ".bak")
    assert json.loads(backup.read_text())["records"] == [{"id": "a"}]


def test_read_recovers_from_corrupted_primary_file_using_backup(tmp_path):
    path = tmp_path / "incidents.json"

    mm.write(path, [{"id": "a"}])
    mm.write(path, [{"id": "a"}, {"id": "b"}])

    path.write_text("{not valid json")

    assert mm.read(path, default=[]) == [{"id": "a"}]


def test_read_returns_default_when_both_primary_and_backup_are_corrupt(tmp_path):
    path = tmp_path / "incidents.json"
    backup = path.with_suffix(path.suffix + ".bak")

    path.write_text("{not valid json")
    backup.write_text("{also not valid")

    assert mm.read(path, default=[]) == []


def test_update_applies_mutation_and_returns_result(tmp_path):
    path = tmp_path / "counter.json"
    mm.write(path, {"count": 1})

    result = mm.update(path, lambda state: {**state, "count": state["count"] + 1}, default={})

    assert result == {"count": 2}
    assert mm.read(path, default={}) == {"count": 2}


def test_update_uses_default_when_file_does_not_exist(tmp_path):
    path = tmp_path / "missing.json"

    result = mm.update(path, lambda records: records + [{"id": "a"}], default=[])

    assert result == [{"id": "a"}]
    assert mm.read(path, default=[]) == [{"id": "a"}]


def test_update_has_no_lost_updates_across_interleaved_read_modify_writes(tmp_path):
    # The exact race update() exists to close: N concurrent increments via
    # load-then-save can drop updates; via update() every one must land.
    import threading

    path = tmp_path / "counter.json"
    mm.write(path, {"count": 0})

    def increment():
        mm.update(path, lambda state: {**state, "count": state.get("count", 0) + 1}, default={})

    threads = [threading.Thread(target=increment) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert mm.read(path, default={})["count"] == 20


def test_concurrent_writes_never_corrupt_the_file(tmp_path):
    import threading

    path = tmp_path / "incidents.json"

    def write_version(n):
        mm.write(path, [{"id": n}])

    threads = [threading.Thread(target=write_version, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = mm.read(path, default=None)
    assert isinstance(result, list) and len(result) == 1
    assert 0 <= result[0]["id"] < 20
