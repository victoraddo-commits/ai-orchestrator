"""Tests for GPU Lifecycle Manager (V3)."""

import pytest
from unittest.mock import patch, MagicMock


class TestPodStateTransitions:
    """Pod state machine transitions."""

    def test_initial_state_is_offline(self, monkeypatch):
        from core.gpu_lifecycle import load_pod_states, POD_A, POD_B

        # Mock load to return empty list (fresh state)
        monkeypatch.setattr(
            "core.gpu_lifecycle.load",
            lambda name: [],
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.save",
            lambda name, data: None,
        )

        states = load_pod_states()
        assert states[POD_A.pod_id]["state"] == "OFFLINE"
        assert states[POD_B.pod_id]["state"] == "OFFLINE"

    def test_valid_transition_offline_to_starting(self, monkeypatch):
        from core.gpu_lifecycle import transition_pod, POD_A, _pod_state_default

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: _pod_state_default(POD_A)},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.save_pod_states",
            lambda states: None,
        )

        assert transition_pod(POD_A.pod_id, "STARTING") is True

    def test_invalid_transition_rejected(self, monkeypatch):
        from core.gpu_lifecycle import transition_pod, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)
        state["state"] = "OFFLINE"

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.save_pod_states",
            lambda states: None,
        )

        # OFFLINE -> BUSY is not allowed (must go through STARTING/READY)
        assert transition_pod(POD_A.pod_id, "BUSY") is False

    def test_full_lifecycle_offline_to_ready(self, monkeypatch):
        from core.gpu_lifecycle import transition_pod, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.save_pod_states",
            lambda states: None,
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.check_pod_health",
            lambda config, timeout: True,
        )

        assert transition_pod(POD_A.pod_id, "STARTING") is True
        assert state["state"] == "STARTING"
        assert state["startup_count"] == 1

        assert transition_pod(POD_A.pod_id, "HEALTH_CHECK") is True
        assert state["state"] == "HEALTH_CHECK"

        assert transition_pod(POD_A.pod_id, "READY") is True
        assert state["state"] == "READY"


class TestQueueBasedDecisions:
    """Auto-start/stop based on queue depth."""

    def test_should_start_when_queue_has_work(self, monkeypatch):
        from core.gpu_lifecycle import gpu_should_start, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)
        state["state"] = "OFFLINE"

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle._count_builds_by_status",
            lambda statuses: 3,  # 3 builds in GENERATOR queue
        )

        assert gpu_should_start(POD_A) is True

    def test_should_not_start_when_already_ready(self, monkeypatch):
        from core.gpu_lifecycle import gpu_should_start, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)
        state["state"] = "READY"

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle._count_builds_by_status",
            lambda statuses: 3,
        )

        # Already READY — should NOT start again
        assert gpu_should_start(POD_A) is False

    def test_should_not_start_when_queue_empty(self, monkeypatch):
        from core.gpu_lifecycle import gpu_should_start, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)
        state["state"] = "OFFLINE"

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle._count_builds_by_status",
            lambda statuses: 0,
        )

        assert gpu_should_start(POD_A) is False

    def test_should_stop_after_idle_timeout(self, monkeypatch):
        from core.gpu_lifecycle import gpu_should_stop, POD_A, _pod_state_default
        from datetime import datetime, timezone, timedelta

        state = _pod_state_default(POD_A)
        state["state"] = "READY"
        # Last activity was 15 minutes ago
        old_time = datetime.now(timezone.utc) - timedelta(seconds=900)
        state["last_activity"] = old_time.isoformat()

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle._count_builds_by_status",
            lambda statuses: 0,
        )

        assert gpu_should_stop(POD_A) is True


class TestCostTracking:
    """Cost and metrics tracking."""

    def test_cost_accumulates_on_active_time(self, monkeypatch):
        from core.gpu_lifecycle import transition_pod, POD_A, _pod_state_default

        state = _pod_state_default(POD_A)
        state["state"] = "READY"

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {POD_A.pod_id: state},
        )
        monkeypatch.setattr(
            "core.gpu_lifecycle.save_pod_states",
            lambda states: None,
        )

        # Go BUSY then back to READY
        transition_pod(POD_A.pod_id, "BUSY")
        state["active_since"] = None  # simulate instant completion
        transition_pod(POD_A.pod_id, "READY")

        # Tasks should increment
        assert state["tasks_completed"] >= 1

    def test_metrics_includes_both_pods(self, monkeypatch):
        from core.gpu_lifecycle import get_pod_metrics, POD_A, POD_B

        monkeypatch.setattr(
            "core.gpu_lifecycle.load_pod_states",
            lambda: {},
        )

        metrics = get_pod_metrics()
        assert len(metrics) == 2
        pod_ids = {m["pod_id"] for m in metrics}
        assert POD_A.pod_id in pod_ids
        assert POD_B.pod_id in pod_ids


class TestPodRoles:
    """Pod A = Generator, Pod B = Reviewer."""

    def test_pod_a_is_generator(self):
        from core.gpu_lifecycle import POD_A
        assert POD_A.role == "GENERATOR"

    def test_pod_b_is_reviewer(self):
        from core.gpu_lifecycle import POD_B
        assert POD_B.role == "REVIEWER"

    def test_pods_have_different_endpoints(self):
        from core.gpu_lifecycle import POD_A, POD_B
        assert POD_A.pod_id != POD_B.pod_id
        assert POD_A.endpoint_url != POD_B.endpoint_url
