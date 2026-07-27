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
