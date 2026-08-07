import os
import stat
from pathlib import Path

import pytest
from core.k3.snapshot import WorkspaceSnapshooter, WorkspaceSnapshot, ChangeSet


class TestChangeSet:
    def test_empty(self):
        cs = ChangeSet()
        assert cs.has_changes() is False
        assert cs.total_changes() == 0

    def test_with_changes(self):
        cs = ChangeSet(created=[Path("a.txt")], modified=[Path("b.txt")], deleted=[Path("c.txt")])
        assert cs.has_changes() is True
        assert cs.total_changes() == 3

    def test_to_dict(self):
        cs = ChangeSet(created=[Path("x")], modified=[Path("y")], deleted=[Path("z")])
        d = cs.to_dict()
        assert d == {"created": ["x"], "modified": ["y"], "deleted": ["z"]}


class TestWorkspaceSnapshooter:
    def test_capture_empty_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()
        assert snapshot.entries == {}

    def test_capture_with_files(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a.txt").write_text("hello")
        (ws / "sub").mkdir()
        (ws / "sub" / "b.txt").write_text("world")

        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        assert "a.txt" in snapshot.entries
        assert "sub/b.txt" in snapshot.entries
        assert snapshot.entries["a.txt"]["type"] == "file"
        assert "hash" in snapshot.entries["a.txt"]
        assert snapshot.entries["a.txt"]["size"] == 5

    def test_captured_hashes_differ_for_different_content(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a.txt").write_text("hello")
        (ws / "b.txt").write_text("world")

        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        assert snapshot.entries["a.txt"]["hash"] != snapshot.entries["b.txt"]["hash"]

    def test_captured_hashes_match_for_identical_content(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a.txt").write_text("hello")
        (ws / "b.txt").write_text("hello")

        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        assert snapshot.entries["a.txt"]["hash"] == snapshot.entries["b.txt"]["hash"]

    def test_capture_skips_directories(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "sub").mkdir()
        (ws / "sub" / "nested").mkdir()
        (ws / "a.txt").write_text("hi")

        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        assert "sub" not in snapshot.entries
        assert "sub/nested" not in snapshot.entries
        assert "a.txt" in snapshot.entries


class TestWorkspaceSnapshot:
    def test_to_dict(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a.txt").write_text("hello")
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()
        d = snapshot.to_dict()
        assert d["file_count"] == 1
        assert d["workspace_path"] == str(ws)

    def test_diff_detects_created_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "newfile.txt").write_text("new content")

        changes = snapshot.diff(upper)
        assert len(changes.created) == 1
        assert str(changes.created[0]) == "newfile.txt"

    def test_diff_detects_modified_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("original content")
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "file.txt").write_text("modified content")

        changes = snapshot.diff(upper)
        assert len(changes.modified) == 1
        assert str(changes.modified[0]) == "file.txt"

    def test_diff_no_changes_when_identical(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("same content")
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "file.txt").write_text("same content")

        changes = snapshot.diff(upper)
        assert changes.has_changes() is False

    def test_diff_whiteout_detected_as_deleted(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("original content")
        snapshooter = WorkspaceSnapshooter(str(ws))
        snapshot = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / ".wh.file.txt").write_text("")

        changes = snapshot.diff(upper)
        assert len(changes.deleted) == 1
        assert str(changes.deleted[0]) == ".wh.file.txt"
