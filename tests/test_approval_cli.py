from core.approval import create_request
from core.approval_cli import run_transition, get_operator_identity
from core import approval


def test_declining_confirmation_does_not_approve():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    result = run_transition(request["id"], approval.approve, "approve", confirm_fn=lambda prompt: False)

    assert result is None
    assert approval.load_requests()[0]["status"] == "pending"


def test_confirming_approves_and_records_operator(monkeypatch):
    monkeypatch.setattr("core.approval_cli.get_operator_identity", lambda: "alice")

    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    result = run_transition(request["id"], approval.approve, "approve", confirm_fn=lambda prompt: True)

    assert result["status"] == "approved"
    assert result["approved_by"] == "alice"


def test_skip_confirm_bypasses_prompt_entirely():
    request = create_request("restart_container", "svc-a", "reason", incident_id="inc1")

    result = run_transition(request["id"], approval.approve, "approve", skip_confirm=True)

    assert result["status"] == "approved"


def test_get_operator_identity_prefers_sudo_user(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "bob")

    assert get_operator_identity() == "bob"
