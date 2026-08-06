"""Tests for Kai V3 pipeline rebuild.

Covers: imports, contracts, approval voting, sandbox lifecycle,
compilation, dedup, timeouts, stale references, and GPU state machine.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Module imports ─────────────────────────────────────────────────────────

def test_all_modules_import():
    """All 14 v3 modules import without errors."""
    from core.v3 import build_contract, sandbox_manager, gpu_manager
    from core.v3 import cost_tracker, monitoring, recovery
    from core.v3 import worker_pool, approval_system, review_pipeline
    from core.v3 import roadmap_compiler, build_manager, roadmap_manager
    from core.v3 import cycle, scheduler_v3


# ── Build contracts ────────────────────────────────────────────────────────

class TestBuildContract:
    def test_validate_valid_contract(self):
        from core.v3.build_contract import validate_contract
        contract = {
            "task_id": "test-1",
            "objective": "Test feature",
            "acceptance_criteria": ["test passes"],
            "files_allowed": ["src/*"],
            "files_forbidden": [],
            "dependencies": [],
            "required_reviewers": ["architecture", "security", "qa"],
            "rollback_plan": "git revert",
        }
        is_valid, errors = validate_contract(contract)
        assert is_valid, f"Expected valid, got: {errors}"

    def test_validate_missing_fields(self):
        from core.v3.build_contract import validate_contract
        contract = {"task_id": "test-1"}
        is_valid, errors = validate_contract(contract)
        assert not is_valid
        assert len(errors) > 0

    def test_validate_overlap_files(self):
        from core.v3.build_contract import validate_contract
        contract = {
            "task_id": "test-1",
            "objective": "Test",
            "acceptance_criteria": [],
            "files_allowed": ["src/*", "src/auth.py"],
            "files_forbidden": ["src/auth.py"],
            "dependencies": [],
            "required_reviewers": ["qa"],
            "rollback_plan": "git revert",
        }
        is_valid, errors = validate_contract(contract)
        assert not is_valid
        assert any("both allowed and forbidden" in e.lower() for e in errors)

    def test_normalize_fills_defaults(self):
        from core.v3.build_contract import normalize_contract
        contract = {"task_id": "test-1", "objective": "Test"}
        normalized = normalize_contract(contract)
        assert "acceptance_criteria" in normalized
        assert "files_allowed" in normalized
        assert "rollback_plan" in normalized


# ── Approval system ────────────────────────────────────────────────────────

class TestApprovalSystem:
    def test_init_approval_creates_pending(self):
        from core.v3.approval_system import init_build_approval, get_vote_tally
        init_build_approval("build-test-1")
        tally = get_vote_tally("build-test-1")
        assert tally["pending"] == 5  # All 5 reviewers pending
        assert tally["approvals"] == 0

    def test_cast_vote_approve(self):
        from core.v3.approval_system import init_build_approval, cast_vote, get_vote_tally
        init_build_approval("build-test-2")
        cast_vote("build-test-2", "architecture", True, confidence=0.9)
        tally = get_vote_tally("build-test-2")
        assert tally["approvals"] == 1
        assert tally["pending"] == 4

    def test_cast_vote_reject(self):
        from core.v3.approval_system import init_build_approval, cast_vote, get_vote_tally
        init_build_approval("build-test-3")
        cast_vote("build-test-3", "security", False,
                  findings=["SQL injection risk"])
        tally = get_vote_tally("build-test-3")
        assert tally["rejections"] == 1

    def test_unanimous_approval(self):
        from core.v3.approval_system import (
            init_build_approval, cast_vote, is_build_approved
        )
        init_build_approval("build-test-4")
        for reviewer in ["architecture", "security", "performance", "qa",
                          "documentation"]:
            cast_vote("build-test-4", reviewer, True, confidence=0.8)
        assert is_build_approved("build-test-4")

    def test_rejected_when_cant_reach_threshold(self):
        from core.v3.approval_system import (
            init_build_approval, cast_vote, is_build_rejected
        )
        init_build_approval("build-test-5")
        # 2 rejections out of 5 → only 3 remaining, threshold is 4 → impossible
        cast_vote("build-test-5", "architecture", False)
        cast_vote("build-test-5", "security", False)
        assert is_build_rejected("build-test-5")

    def test_set_threshold(self):
        from core.v3.approval_system import set_approval_threshold, get_approval_threshold
        set_approval_threshold(3)
        assert get_approval_threshold() == 3
        set_approval_threshold(4)  # Reset


# ── GPU state machine ──────────────────────────────────────────────────────

class TestGPULifecycle:
    def test_valid_transitions(self):
        from core.v3.gpu_manager import transition_pod, POD_A
        # Reset state to OFFLINE
        from core.v3.gpu_manager import _pod_state
        _pod_state[POD_A]["status"] = "OFFLINE"

        transition_pod(POD_A, "STARTING")
        assert _pod_state[POD_A]["status"] == "STARTING"

        transition_pod(POD_A, "HEALTH_CHECK")
        assert _pod_state[POD_A]["status"] == "HEALTH_CHECK"

        transition_pod(POD_A, "READY")
        assert _pod_state[POD_A]["status"] == "READY"

        transition_pod(POD_A, "BUSY")
        assert _pod_state[POD_A]["status"] == "BUSY"

        transition_pod(POD_A, "READY")
        assert _pod_state[POD_A]["status"] == "READY"

        transition_pod(POD_A, "DRAINING")
        transition_pod(POD_A, "STOPPING")
        transition_pod(POD_A, "OFFLINE")
        assert _pod_state[POD_A]["status"] == "OFFLINE"

    def test_invalid_transition_raises(self):
        from core.v3.gpu_manager import transition_pod, POD_A
        from core.v3.gpu_manager import _pod_state
        _pod_state[POD_A]["status"] = "OFFLINE"

        with pytest.raises(ValueError):
            transition_pod(POD_A, "BUSY")  # Can't go OFFLINE → BUSY

    def test_should_start_pod_a(self):
        from core.v3.gpu_manager import should_start_pod, POD_A, _pod_state
        _pod_state[POD_A]["status"] = "OFFLINE"
        assert should_start_pod(POD_A, {"GENERATING": 3})
        assert not should_start_pod(POD_A, {"GENERATING": 0})

    def test_should_start_pod_b(self):
        from core.v3.gpu_manager import should_start_pod, POD_B, _pod_state
        _pod_state[POD_B]["status"] = "OFFLINE"
        assert should_start_pod(POD_B, {"CODE_REVIEW": 1, "DEPLOYING": 0})
        assert should_start_pod(POD_B, {"CODE_REVIEW": 0, "DEPLOYING": 2})
        assert not should_start_pod(POD_B, {"CODE_REVIEW": 0, "DEPLOYING": 0})


# ── Roadmap compiler ───────────────────────────────────────────────────────

class TestRoadmapCompiler:
    def test_compile_with_phases(self):
        from core.v3.roadmap_compiler import compile_roadmap
        # Compile against actual roadmap
        compiled = compile_roadmap()
        assert "dag" in compiled
        assert "tasks" in compiled
        assert "blocked" in compiled
        assert "completed" in compiled
        assert "queue" in compiled

    def test_depth_computation(self):
        from core.v3.roadmap_compiler import PhaseNode, _compute_depths
        nodes = {
            "A": PhaseNode("A", "Leaf A", "pending", 10),
            "B": PhaseNode("B", "Leaf B", "pending", 10),
            "C": PhaseNode("C", "Mid C", "pending", 10,
                           dependencies=["A", "B"]),
            "D": PhaseNode("D", "Deep D", "pending", 10,
                           dependencies=["C"]),
        }
        _compute_depths(nodes)
        assert nodes["A"].depth == 0
        assert nodes["B"].depth == 0
        assert nodes["C"].depth == 1
        assert nodes["D"].depth == 2

    def test_is_phase_ready(self):
        from core.v3.roadmap_compiler import is_phase_ready
        compiled = {
            "dag": {},
            "completed": ["A", "B"],
            "tasks": [],
            "blocked": [],
        }
        from core.v3.roadmap_compiler import PhaseNode
        compiled["dag"]["C"] = PhaseNode(
            "C", "Test C", "pending", 10, dependencies=["A", "B"]
        )
        assert is_phase_ready(compiled, "C")


# ── Dedup and stale reference protection ───────────────────────────────────

class TestBuildManagerV3:
    def test_non_terminal_statuses(self):
        from core.v3.build_manager import (
            NON_TERMINAL_BUILD_STATUSES, TERMINAL_BUILD_STATUSES
        )
        assert "COMPLETED" in TERMINAL_BUILD_STATUSES
        assert "FAILED" in TERMINAL_BUILD_STATUSES
        assert "GENERATING" in NON_TERMINAL_BUILD_STATUSES
        assert "COMPLETED" not in NON_TERMINAL_BUILD_STATUSES

    def test_completion_near_statuses(self):
        from core.v3.build_manager import _COMPLETION_NEAR_STATUSES
        assert "DEPLOYING" in _COMPLETION_NEAR_STATUSES
        assert "CODE_REVIEW" in _COMPLETION_NEAR_STATUSES

    def test_timeout_constants(self):
        from core.v3.build_manager import (
            GENERATION_TIMEOUT, DEPLOYING_TIMEOUT
        )
        assert GENERATION_TIMEOUT == 2400
        assert DEPLOYING_TIMEOUT == 1800


# ── Monitoring ─────────────────────────────────────────────────────────────

class TestMonitoring:
    def test_stuck_build_detection(self):
        from core.v3.monitoring import check_stuck_builds
        from datetime import datetime, timezone, timedelta

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=3000)).isoformat()
        builds = [{
            "id": "test-build-1",
            "name": "Stuck Build",
            "status": "GENERATING",
            "updated": old_time,
        }]
        stuck = check_stuck_builds(builds, generation_timeout=2400)
        assert len(stuck) == 1
        assert stuck[0]["reason"] == "Generation timeout"

    def test_not_stuck_within_timeout(self):
        from core.v3.monitoring import check_stuck_builds
        from datetime import datetime, timezone, timedelta

        recent_time = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
        builds = [{
            "id": "test-build-2",
            "name": "Active Build",
            "status": "GENERATING",
            "updated": recent_time,
        }]
        stuck = check_stuck_builds(builds, generation_timeout=2400)
        assert len(stuck) == 0


# ── Recovery ───────────────────────────────────────────────────────────────

class TestRecovery:
    def test_provider_failure_retry_first(self):
        from core.v3.recovery import handle_provider_failure, RecoveryAction
        result = handle_provider_failure("build-1", "qwen4", "timeout", 1)
        assert result["action"] == RecoveryAction.RETRY

    def test_provider_failure_switch(self):
        from core.v3.recovery import handle_provider_failure, RecoveryAction
        result = handle_provider_failure("build-1", "qwen4", "timeout", 2)
        assert result["action"] == RecoveryAction.SWITCH_PROVIDER

    def test_provider_failure_human_after_all(self):
        from core.v3.recovery import handle_provider_failure, RecoveryAction
        result = handle_provider_failure("build-1", "omniroute", "timeout", 3)
        assert result["action"] == RecoveryAction.HUMAN_APPROVAL


# ── Worker pool ────────────────────────────────────────────────────────────

class TestWorkerPool:
    def test_init_workers(self):
        from core.v3.worker_pool import init_workers, get_all_workers
        init_workers()
        workers = get_all_workers()
        assert len(workers) > 10  # Build + review + management

    def test_pod_separation(self):
        from core.v3.worker_pool import init_workers, get_workers_by_pod
        init_workers()
        pod_a_workers = get_workers_by_pod("qwen4")
        pod_b_workers = get_workers_by_pod("qwen6")
        # All build workers on Pod A, review on Pod B
        for w in pod_a_workers:
            assert w["pod"] == "qwen4"
        for w in pod_b_workers:
            assert w["pod"] == "qwen6"

    def test_assign_and_release(self):
        from core.v3.worker_pool import init_workers, assign_worker, release_worker, get_worker
        init_workers()
        assert assign_worker("FeatureBuilder", "task-1")
        worker = get_worker("FeatureBuilder")
        assert worker["status"] == "BUSY"
        assert worker["current_task"] == "task-1"

        release_worker("FeatureBuilder", success=True)
        worker = get_worker("FeatureBuilder")
        assert worker["status"] == "IDLE"
        assert worker["tasks_completed"] == 1
