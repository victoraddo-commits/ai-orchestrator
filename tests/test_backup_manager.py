"""Unit tests for core.backup_manager (roadmap 27E).

Regression coverage for the 2026-09-18 TOCTOU: ``tarfile.add`` stats each
member lazily, so a file deleted between the directory listing and the stat
(``memory/`` churns via atomic tmp+rename) raised ``FileNotFoundError`` and
aborted the entire application-state backup.
"""
from __future__ import annotations

import tarfile

import core.backup_manager as backup_manager

VANISHING = "builds.json"


def test_add_tree_skips_file_vanishing_mid_walk(tmp_path, monkeypatch):
    """A member deleted mid-walk is skipped, the rest of the tree survives."""
    src = tmp_path / "memory"
    src.mkdir(exist_ok=True)
    (src / "keep.json").write_text('{"ok": true}')
    (src / VANISHING).write_text("about to be renamed away")

    real_gettarinfo = tarfile.TarFile.gettarinfo

    def flaky_gettarinfo(self, name=None, arcname=None, fileobj=None):
        if name is not None and str(name).endswith(VANISHING):
            raise FileNotFoundError(name)
        return real_gettarinfo(self, name, arcname, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", flaky_gettarinfo)

    archive = tmp_path / "state.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        included = backup_manager._add_tree(tar, src, "memory")

    assert included is True
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "memory/keep.json" in names
    assert not any(VANISHING in n for n in names)


def test_add_tree_missing_root_is_skipped(tmp_path):
    """A whole source that vanished before the walk returns False, not raises."""
    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        included = backup_manager._add_tree(tar, tmp_path / "gone", "gone")

    assert included is False
    with tarfile.open(archive, "r:gz") as tar:
        assert tar.getnames() == []
