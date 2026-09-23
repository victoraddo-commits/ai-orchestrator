"""Atomicity regression tests for Second Brain manifest writes.

Task 11 found ``operational/manifest.json`` intermittently corrupted (a stray
trailing ``}``) because ``sb_compact.py`` rewrote it with a non-atomic
``json.dump(m, open(path, "w"))`` while the live scheduler read/wrote the same
file. ``AppendOnlyStore._write_manifest`` had the same in-place truncating
write, so two writers racing left bytes from the longer write behind.

These tests pin the contract: writers must never open the live manifest for
in-place writing. They write a sibling temp file and ``os.replace()`` it, so a
concurrent reader always sees either the previous complete file or the new
complete file -- never a truncated/partial one.
"""
from __future__ import annotations

import builtins
import json
import os
import threading

import sb_compact
from core.second_brain.base_store import AppendOnlyStore


def _valid_manifest(**over):
    m = {"schema_version": 1, "merge_policy": "newest_wins",
         "store_name": "operational", "record_count": 1}
    m.update(over)
    return m


def _spy_in_place_writes(monkeypatch, target):
    """Record any ``open(target, "...w...")`` -- the corrupting pattern."""
    in_place = []
    real_open = builtins.open

    def spy_open(file, mode="r", *args, **kwargs):
        try:
            path = os.fspath(file)
        except TypeError:
            path = None
        if path is not None and path == os.fspath(target) and "w" in mode:
            in_place.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    return in_place


def _seed_operational(tmp_path, monkeypatch):
    monkeypatch.setattr(sb_compact, "STORES_ROOT", str(tmp_path))
    d = tmp_path / "operational"
    d.mkdir()
    (d / "records.jsonl").write_text(
        json.dumps({"id": "r1", "entity": "e1"}) + "\n")
    (d / "current_index.json").write_text(json.dumps({"e1": "r1"}))
    manifest = d / "manifest.json"
    manifest.write_text(json.dumps(_valid_manifest()))
    return manifest


def test_base_store_manifest_never_opened_in_place(tmp_path, monkeypatch):
    store = AppendOnlyStore(str(tmp_path))
    manifest = store.manifest_file
    manifest.write_text(json.dumps(_valid_manifest(record_count=1)))
    in_place = _spy_in_place_writes(monkeypatch, manifest)
    store._write_manifest(2)
    assert in_place == [], "manifest must not be opened for in-place write"
    assert json.loads(manifest.read_text())["record_count"] == 2


def test_compact_manifest_never_opened_in_place(tmp_path, monkeypatch):
    manifest = _seed_operational(tmp_path, monkeypatch)
    in_place = _spy_in_place_writes(monkeypatch, manifest)
    sb_compact.compact("operational")
    assert in_place == [], "manifest must not be opened for in-place write"
    assert json.loads(manifest.read_text())["record_count"] == 1


def test_concurrent_readers_never_see_partial_manifest(tmp_path, monkeypatch):
    """Simulate the scheduler reading while compaction rewrites the manifest.

    Before the fix this reproduced ``JSONDecodeError`` (truncated/empty file)
    and, when a shorter write followed a longer one, a stray trailing ``}``.
    """
    manifest = _seed_operational(tmp_path, monkeypatch)

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                with open(manifest) as f:
                    json.load(f)
            except Exception as exc:  # noqa: BLE001 - the point of the test
                errors.append(repr(exc))

    def writer():
        for i in range(300):
            sb_compact._atomic_write_json(
                manifest, _valid_manifest(record_count=i, pad="x" * 2000))

    t = threading.Thread(target=reader)
    t.start()
    try:
        writer()
    finally:
        stop.set()
        t.join()

    assert errors == [], errors[:3]
    assert json.loads(manifest.read_text())["record_count"] == 299
