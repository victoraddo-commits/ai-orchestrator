"""Account Registry (core/accounts) — TDD suite.

Isolation: AI_ORCHESTRATOR_MEMORY_DIR -> tmp dir. Event bus and audit spied.
"""

import importlib
import json

import pytest

from core.accounts import (
    AccountRecord,
    AccountCreate,
    AccountUpdate,
    VerificationStatus,
    SecurityStatus,
    AccountStatus,
    DuplicateAccount,
    AccountNotFound,
    vault_reference_for,
    create_account,
    get_account,
    list_accounts,
    update_account,
    archive_account,
    find_accounts,
    exists,
    set_verification_status,
    set_security_status,
    link_mission,
    link_identity,
    link_vault_reference,
)
from core.agentguard.guard import (
    AgentGuard,
    ActionRequest,
    ActionType,
    RiskLevel,
    Decision,
)


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def bus_spy(monkeypatch):
    events = []

    def _publish(topic, payload, *a, **k):
        events.append((topic, payload))
        return 0

    monkeypatch.setattr("core.kai_event_bus.publish", _publish)
    return events


@pytest.fixture
def audit_spy(monkeypatch):
    events = []

    def _audit(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("core.audit_logger.log_audit_event", _audit)
    return events


def _create(**overrides):
    payload = {
        "provider": "github",
        "account_type": "developer",
        "username": "kai-dev",
        "email": "kai-gh@example.com",
    }
    payload.update(overrides)
    return create_account(AccountCreate(**payload))


def test_verification_status_enum_values():
    assert VerificationStatus.VERIFIED.value == "VERIFIED"
    assert VerificationStatus.PARTIALLY_VERIFIED.value == "PARTIALLY VERIFIED"
    assert VerificationStatus.PENDING.value == "PENDING"
    assert VerificationStatus.FAILED.value == "FAILED"
    assert VerificationStatus.NOT_TESTED.value == "NOT TESTED"
    assert VerificationStatus.NOT_APPLICABLE.value == "NOT APPLICABLE"


def test_vault_reference_shape():
    assert vault_reference_for("GitHub", "acct-1") == "secrets/accounts/github/acct-1"


def test_create_account_basic():
    acct = _create()
    assert isinstance(acct, AccountRecord)
    assert acct.account_id.startswith("acct-")
    assert acct.provider == "github"
    assert acct.verification_status == VerificationStatus.NOT_TESTED
    assert acct.security_status == SecurityStatus.UNKNOWN
    assert acct.risk_level == RiskLevel.MEDIUM
    assert acct.status == AccountStatus.active
    assert acct.vault_reference == f"secrets/accounts/github/{acct.account_id}"
    assert acct.creation_time


def test_create_account_rejects_password_field():
    with pytest.raises(Exception):
        AccountCreate(provider="github", password="hunter2")


def test_get_list_update_archive():
    acct = _create()
    assert get_account(acct.account_id).account_id == acct.account_id
    assert get_account("nope") is None
    assert len(list_accounts(provider="github")) == 1
    updated = update_account(acct.account_id, AccountUpdate(username="renamed"))
    assert updated.username == "renamed"
    assert archive_account(acct.account_id).status == AccountStatus.archived


def test_update_missing_raises():
    with pytest.raises(AccountNotFound):
        update_account("nope", AccountUpdate(username="x"))


def test_duplicate_prevention_exists():
    _create(email="dup@example.com", username="dupuser")
    assert exists("github", {"email": "dup@example.com"}) is True
    assert exists("github", {"username": "dupuser"}) is True
    assert exists("github", "dup@example.com") is True
    assert exists("gitlab", {"email": "dup@example.com"}) is False
    assert exists("github", {"email": "nobody@example.com"}) is False
    with pytest.raises(DuplicateAccount):
        _create(email="dup@example.com", username="another")


def test_find_accounts():
    _create(email="find@example.com", username="findme")
    found = find_accounts("github", email="find@example.com")
    assert len(found) == 1
    assert found[0].username == "findme"


def test_status_transitions_emit_events(bus_spy, audit_spy):
    acct = _create()
    acct = set_verification_status(acct.account_id, VerificationStatus.VERIFIED)
    assert acct.verification_status == VerificationStatus.VERIFIED
    acct = set_security_status(acct.account_id, SecurityStatus.SECURE)
    assert acct.security_status == SecurityStatus.SECURE
    topics = [t for t, _ in bus_spy]
    assert "account.created" in topics
    assert "account.verification.changed" in topics
    assert "account.security.changed" in topics
    assert any(e["event_type"] == "account.create" for e in audit_spy)
    assert any(e["event_type"] == "account.verification.change" for e in audit_spy)


def test_set_invalid_status_rejected():
    acct = _create()
    with pytest.raises(ValueError):
        set_verification_status(acct.account_id, "BOGUS")


def test_linkers():
    acct = _create()
    acct = link_mission(acct.account_id, "mis-abc")
    acct = link_identity(acct.account_id, "ident-xyz")
    acct = link_vault_reference(acct.account_id, "secrets/accounts/github/custom")
    assert acct.mission_id == "mis-abc"
    assert acct.digital_identity == "ident-xyz"
    assert acct.vault_reference == "secrets/accounts/github/custom"


def test_persistence_survives_reload(isolated_memory):
    acct = _create(email="persist@example.com")
    path = isolated_memory / "account_registry.json"
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["schema_version"] == 1
    import core.accounts.manager as manager

    importlib.reload(manager)
    assert manager.get_account(acct.account_id).email == "persist@example.com"


def test_agentguard_risk_mapping():
    guard = AgentGuard()
    register = guard.check_action(
        ActionRequest(
            agent_id="onboarding",
            user_id="kai",
            action_type=ActionType.ACCOUNT_REGISTER,
            resource="github/kai-dev",
            details="register account",
        )
    )
    assert register.risk_level == RiskLevel.MEDIUM
    assert register.decision == Decision.ALLOW

    recovery = guard.check_action(
        ActionRequest(
            agent_id="onboarding",
            user_id="kai",
            action_type=ActionType.ACCOUNT_RECOVERY,
            resource="github/kai-dev",
            details="change ownership/recovery",
        )
    )
    assert recovery.risk_level == RiskLevel.HIGH
    assert recovery.decision == Decision.REQUIRE_APPROVAL
