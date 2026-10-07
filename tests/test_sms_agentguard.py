"""Additive AgentGuard coverage for the SMS action types (no regressions)."""

from core.agentguard.guard import (
    ActionRequest,
    ActionType,
    AgentGuard,
    Decision,
    RiskLevel,
)


def _check(action, resource="sms/x", details="ingest sms"):
    return AgentGuard().check_action(ActionRequest(
        agent_id="sms_worker", user_id="kai", action_type=action,
        resource=resource, details=details))


def test_sms_receive_is_low_and_allowed():
    result = _check(ActionType.SMS_RECEIVE)
    assert result.risk_level == RiskLevel.LOW
    assert result.decision == Decision.ALLOW


def test_sms_otp_handoff_is_medium_and_allowed():
    result = _check(ActionType.SMS_OTP_HANDOFF)
    assert result.risk_level == RiskLevel.MEDIUM
    assert result.decision == Decision.ALLOW


def test_email_actiontypes_still_present():
    assert ActionType.EMAIL_RECEIVE.value == "email_receive"
    assert ActionType.EMAIL_FOLLOW_LINK.value == "email_follow_link"


def test_critical_actions_still_require_approval():
    result = _check(ActionType.PRIVILEGED)
    assert result.decision == Decision.REQUIRE_APPROVAL
