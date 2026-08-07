import os
import shutil
from pathlib import Path
from fnmatch import fnmatch

import pytest
from core.k3.policy import ChangePolicyEngine
from core.k3.snapshot import WorkspaceSnapshooter, ChangeSet


class TestChangePolicyEngine:
    def test_report_without_baseline(self, tmp_path):
        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "newfile.txt").write_text("content")

        engine = ChangePolicyEngine(str(upper))
        changes = engine.report()
        assert len(changes.created) == 1

    def test_report_with_baseline(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "orig.txt").write_text("original")

        snapshooter = WorkspaceSnapshooter(str(ws))
        baseline = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "orig.txt").write_text("modified")
        (upper / "new.txt").write_text("new")

        engine = ChangePolicyEngine(str(upper), baseline=baseline)
        changes = engine.report()
        assert len(changes.created) == 1
        assert len(changes.modified) == 1

    def test_commit_copies_changes(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "orig.txt").write_text("original")

        snapshooter = WorkspaceSnapshooter(str(ws))
        baseline = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "orig.txt").write_text("modified")
        (upper / "new.txt").write_text("new")
        (upper / "sub").mkdir()
        (upper / "sub" / "nested.txt").write_text("nested")

        engine = ChangePolicyEngine(str(upper), baseline=baseline, workspace_path=str(ws))
        engine.commit()

        assert (ws / "orig.txt").read_text() == "modified"
        assert (ws / "new.txt").read_text() == "new"
        assert (ws / "sub" / "nested.txt").read_text() == "nested"

    def test_commit_deletes_removed_files(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "will_be_deleted.txt").write_text("delete me")
        (ws / "keep.txt").write_text("keep me")

        snapshooter = WorkspaceSnapshooter(str(ws))
        baseline = snapshooter.capture()

        upper = tmp_path / "upper"
        upper.mkdir()

        changes = ChangeSet(deleted=[Path("will_be_deleted.txt")])

        engine = ChangePolicyEngine(str(upper), baseline=baseline, workspace_path=str(ws))
        engine.report = lambda: changes
        engine.commit()

        assert not (ws / "will_be_deleted.txt").exists()
        assert (ws / "keep.txt").exists()

    def test_commit_requires_workspace_path(self, tmp_path):
        upper = tmp_path / "upper"
        upper.mkdir()
        engine = ChangePolicyEngine(str(upper))
        with pytest.raises(ValueError, match="workspace_path"):
            engine.commit()

    def test_extract_artifacts(self, tmp_path):
        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "build").mkdir()
        (upper / "build" / "app.tar.gz").write_text("binary")
        (upper / "build" / "debug.log").write_text("logs")
        (upper / "src").mkdir()
        (upper / "src" / "main.py").write_text("code")

        out = tmp_path / "output"

        engine = ChangePolicyEngine(str(upper))
        engine.extract_artifacts(["build/*.tar.gz", "*.log"], str(out))

        assert (out / "build" / "app.tar.gz").exists()
        expected_log = out / "build" / "debug.log"
        # debug.log is under build/ so "*.log" matches it
        assert expected_log.exists() or any(
            fnmatch(str(p.relative_to(out)), "**/*.log")
            for p in out.rglob("*") if p.is_file()
        )
        assert not (out / "src" / "main.py").exists()

    def test_extract_artifacts_matches_by_basename(self, tmp_path):
        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "dist").mkdir()
        (upper / "dist" / "app.tar.gz").write_text("binary")
        (upper / "other").mkdir()
        (upper / "other" / "lib.tar.gz").write_text("lib")

        out = tmp_path / "output"

        engine = ChangePolicyEngine(str(upper))
        engine.extract_artifacts(["*.tar.gz"], str(out))

        files = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()]
        assert "dist/app.tar.gz" in files
        assert any(f.endswith("lib.tar.gz") for f in files)

    def test_extract_artifacts_requires_output_dir(self, tmp_path):
        upper = tmp_path / "upper"
        upper.mkdir()
        engine = ChangePolicyEngine(str(upper))
        with pytest.raises(ValueError, match="output_dir"):
            engine.extract_artifacts(["*"], None)
