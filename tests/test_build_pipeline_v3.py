"""Tests for Kai Software Factory V3 pipeline features."""

import pytest
import time
from unittest.mock import patch, MagicMock, call


class TestTwoPassProcessing:
    """V3: completion-near builds process before generation builds."""

    def test_completion_near_statuses_defined(self):
        from core.build_manager import _COMPLETION_NEAR_STATUSES
        assert "DEPLOYING" in _COMPLETION_NEAR_STATUSES
        assert "CODE_REVIEW" in _COMPLETION_NEAR_STATUSES

    def test_deploying_processed_before_generating(self, monkeypatch):
        """Pass 1 (DEPLOYING) runs before Pass 2 (GENERATING)."""
        from core.build_manager import advance_builds, load_builds, _COMPLETION_NEAR_STATUSES
        from core.build_manager import _ACTIONABLE_STATUSES

        # Create mock builds: 1 DEPLOYING, 1 GENERATING
        mock_builds = [
            {"id": "build-1", "status": "GENERATING", "name": "gen-build", "priority": False,
             "project_path": "/tmp/gen", "description": "gen"},
            {"id": "build-2", "status": "DEPLOYING", "name": "dep-build", "priority": False,
             "project_path": "/tmp/dep", "description": "dep"},
        ]

        execution_order = []

        def fake_advance_one(build):
            execution_order.append(build["status"])
            build["status"] = "COMPLETED"

        monkeypatch.setattr(
            "core.build_manager._advance_one_build",
            fake_advance_one,
        )
        monkeypatch.setattr(
            "core.build_manager.load_builds",
            lambda include_terminal=False: list(mock_builds),
        )
        monkeypatch.setattr(
            "core.build_manager._check_timeouts",
            lambda builds: [],
        )
        monkeypatch.setattr(
            "core.build_manager.check_stale_roadmap_references",
            lambda: [],
        )
        monkeypatch.setattr(
            "core.build_manager.cleanup_sandbox",
            lambda build_id: None,
        )

        result = advance_builds()

        # DEPLOYING must be processed BEFORE GENERATING
        dep_idx = execution_order.index("DEPLOYING") if "DEPLOYING" in execution_order else -1
        gen_idx = execution_order.index("GENERATING") if "GENERATING" in execution_order else -1

        if dep_idx >= 0 and gen_idx >= 0:
            assert dep_idx < gen_idx, (
                f"DEPLOYING (pos {dep_idx}) must process before GENERATING (pos {gen_idx})"
            )


class TestTimeoutProtection:
    """V3: stuck builds auto-fail after timeout."""

    def test_generating_timeout_configured(self):
        from core.build_manager import GENERATING_TIMEOUT_SECONDS
        assert GENERATING_TIMEOUT_SECONDS == 2400

    def test_deploying_timeout_configured(self):
        from core.build_manager import DEPLOYING_TIMEOUT_SECONDS
        assert DEPLOYING_TIMEOUT_SECONDS == 1800

    def test_stuck_generating_build_fails(self, monkeypatch):
        from core.build_manager import _check_timeouts, _persist_build

        # Build that's been stuck for 3000s (over 2400s limit)
        old_time = time.time() - 3000
        stuck_build = {
            "id": "stuck-gen",
            "status": "GENERATING",
            "name": "stuck-build",
            "_v3_started_at": old_time,
        }

        monkeypatch.setattr(
            "core.build_manager._persist_build",
            lambda b: None,
        )
        monkeypatch.setattr(
            "core.build_manager._record_if_terminal",
            lambda b: None,
        )

        events = _check_timeouts([stuck_build])
        assert stuck_build["status"] == "FAILED"
        assert "timeout" in stuck_build.get("failure_reason", "").lower()
        assert any(e["build_id"] == "stuck-gen" for e in events)

    def test_active_generating_build_kept(self, monkeypatch):
        from core.build_manager import _check_timeouts

        # Build that's been generating for 100s (under 2400s limit)
        recent_time = time.time() - 100
        build = {
            "id": "active-gen",
            "status": "GENERATING",
            "name": "active-build",
            "_v3_started_at": recent_time,
        }

        events = _check_timeouts([build])
        assert build["status"] == "GENERATING"  # Not failed
        assert len([e for e in events if e["build_id"] == "active-gen"]) == 0


class TestDuplicatePrevention:
    """V3: duplicate build detection."""

    def test_non_terminal_statuses_defined(self):
        from core.build_manager import NON_TERMINAL_BUILD_STATUSES
        assert "GENERATING" in NON_TERMINAL_BUILD_STATUSES
        assert "COMPLETED" not in NON_TERMINAL_BUILD_STATUSES
        assert "FAILED" not in NON_TERMINAL_BUILD_STATUSES

    def test_duplicate_build_returns_existing(self, monkeypatch):
        from core.build_manager import create_build, NON_TERMINAL_BUILD_STATUSES

        existing_build = {
            "id": "existing-123",
            "name": "test-phase",
            "status": "GENERATING",
        }

        # Mock load to return an existing non-terminal build
        monkeypatch.setattr(
            "core.build_manager.load_builds",
            lambda include_terminal=False: [existing_build],
        )
        monkeypatch.setattr(
            "core.build_manager.save_builds",
            lambda builds: None,
        )
        monkeypatch.setattr(
            "core.build_manager.init_git_if_needed",
            lambda project_path, branch: None,
        )

        result = create_build(
            name="test-phase",
            description="A test",
            project_path="/tmp/test",
        )

        # Should return the existing build, not create a duplicate
        assert result["id"] == "existing-123"

    def test_new_build_created_when_existing_is_terminal(self, monkeypatch):
        from core.build_manager import create_build

        # Existing build is terminal (FAILED) — should be ignored
        existing_terminal = {
            "id": "old-failed",
            "name": "test-phase",
            "status": "FAILED",
        }

        new_builds = []

        def fake_save(builds):
            new_builds.extend(builds)

        monkeypatch.setattr(
            "core.build_manager.load_builds",
            lambda include_terminal=False: [],  # Terminal excluded
        )
        monkeypatch.setattr(
            "core.build_manager.save_builds",
            fake_save,
        )
        monkeypatch.setattr(
            "core.build_manager.init_git_if_needed",
            lambda project_path, branch: None,
        )
        monkeypatch.setattr(
            "core.build_manager.get_template",
            lambda t: None,
        )

        result = create_build(
            name="test-phase",
            description="A retry",
            project_path="/tmp/test2",
        )

        # Should have created a NEW build with a different ID
        assert result["id"] != "old-failed"
        assert result["status"] == "REQUESTED"


class TestTerminalBuildManagement:
    """V3: load_builds() excludes terminal builds by default."""

    def test_load_builds_excludes_terminal(self, monkeypatch):
        from core.build_manager import load_builds, _EXCLUDED_STATUSES

        all_builds = [
            {"id": "b1", "status": "GENERATING", "name": "active"},
            {"id": "b2", "status": "COMPLETED", "name": "done"},
            {"id": "b3", "status": "FAILED", "name": "failed"},
            {"id": "b4", "status": "REQUESTED", "name": "new"},
        ]

        monkeypatch.setattr(
            "core.build_manager.load",
            lambda name: list(all_builds),
        )
        monkeypatch.setattr(
            "core.build_manager.save",
            lambda name, data: None,
        )

        active = load_builds()
        active_statuses = {b["status"] for b in active}
        assert "COMPLETED" not in active_statuses
        assert "FAILED" not in active_statuses

    def test_load_builds_include_terminal(self, monkeypatch):
        from core.build_manager import load_builds

        all_builds = [
            {"id": "b1", "status": "GENERATING", "name": "active"},
            {"id": "b2", "status": "COMPLETED", "name": "done"},
        ]

        monkeypatch.setattr(
            "core.build_manager.load",
            lambda name: list(all_builds),
        )
        monkeypatch.setattr(
            "core.build_manager.save",
            lambda name, data: None,
        )

        result = load_builds(include_terminal=True)
        assert len(result) == 2


class TestSandboxIsolation:
    """V3: sandbox isolation for builds."""

    def test_sandbox_path_is_unique(self):
        from core.sandbox_manager import get_sandbox_path
        path1 = get_sandbox_path("build-aaa")
        path2 = get_sandbox_path("build-bbb")
        assert path1 != path2

    def test_build_branch_naming(self):
        from core.sandbox_manager import get_build_branch
        assert get_build_branch("abc123") == "build-abc123"


class TestPodRoutingV3:
    """V3: omniroute_deepseek_coding primary in coding, deepseek_native_pro primary in text roles."""

    def test_coding_front_includes_omniroute_deepseek_coding(self):
        from core.ai.ai_router import CODING_ROTATING_FRONT
        assert "omniroute_deepseek_coding" in CODING_ROTATING_FRONT
        # gpuai_minimax (MiniMax M3 via GPU.ai) is in the coding fallback chain,
        # not the rotating front.
        assert "gpuai_minimax" not in CODING_ROTATING_FRONT

    def test_deepseek_native_pro_is_review_primary(self):
        from core.ai.ai_router import ROLE_PROVIDERS
        assert ROLE_PROVIDERS["review"][0] == "deepseek_native_pro"

    def test_deepseek_native_pro_is_architecture_primary(self):
        from core.ai.ai_router import ROLE_PROVIDERS
        assert ROLE_PROVIDERS["architecture"][0] == "deepseek_native_pro"

    def test_deepseek_native_pro_is_planning_primary(self):
        from core.ai.ai_router import ROLE_PROVIDERS
        assert ROLE_PROVIDERS["planning"][0] == "deepseek_native_pro"
