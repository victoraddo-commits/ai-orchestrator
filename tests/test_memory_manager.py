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
