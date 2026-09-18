import pytest

from core.docker_actions import execute_action
from core.security import SecurityViolation


def test_execute_action_blocks_unclassified_action():
    with pytest.raises(SecurityViolation):
        execute_action("delete_everything", "svc-a")


def test_execute_action_still_restarts_known_low_risk_action():
    result = execute_action("restart_container", "definitely-not-a-real-container-xyz")

    assert result["status"] == "failed"
    assert result["reason"] == "container not found"


def test_container_status_returns_unknown_when_docker_absent(monkeypatch):
    import core.docker_actions as docker_actions

    monkeypatch.setattr(docker_actions.shutil, "which", lambda name: None)

    assert docker_actions.container_status("anything") == "unknown"


def test_container_exists_false_when_docker_absent(monkeypatch):
    import core.docker_actions as docker_actions

    monkeypatch.setattr(docker_actions.shutil, "which", lambda name: None)

    assert docker_actions.container_exists("anything") is False


def test_execute_action_fails_closed_when_docker_call_raises(monkeypatch):
    import core.docker_actions as docker_actions

    def boom(service):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(docker_actions, "restart_container", boom)

    result = docker_actions.execute_action("restart_container", "svc-a")

    assert result["status"] == "failed"
    assert "FileNotFoundError" in result["reason"]
