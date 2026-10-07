"""Browser-operator state layer — fast orchestrator-side tests (no Chromium).

Exercises security controls, operations validation, detectors, profile/session
stores, the takeover state machine, credential domain refusal, redirect
blocking, event/audit emission and concurrency caps using an in-memory engine.
"""

from __future__ import annotations

import pytest

from core.browser import security
from core.browser.detectors import (
    detect_auth_state,
    detect_captcha,
    detect_error,
    detect_mfa_prompt,
    detect_verification_page,
)
from core.browser.manager import BrowserOperator, TooManySessions
from core.browser.operations import InvalidOperation, validate_operation
from core.browser.schema import PageSnapshot
from core.browser.security import CredentialTargetRefused, RedirectBlocked
from core.browser.sinks import RecordingSink

from fake_engine import FakeEngine


@pytest.fixture
def operator(tmp_path):
    op = BrowserOperator(tmp_path, engine=FakeEngine(), sink=RecordingSink())
    op.set_provider_domain("provider", "accounts.example.test")
    return op


# -- security ------------------------------------------------------------------


def test_navigation_url_scheme_is_restricted():
    with pytest.raises(security.UnsafeUrl):
        security.validate_navigation_url("javascript:alert(1)")
    with pytest.raises(security.UnsafeUrl):
        security.validate_navigation_url("file:///etc/passwd")
    assert security.validate_navigation_url("https://a.example.com/x") == "https://a.example.com/x"


def test_same_site_matching():
    assert security.is_same_site("accounts.example.com", "example.com") is True
    assert security.is_same_site("example.com", "example.com") is True
    assert security.is_same_site("evil.com", "example.com") is False
    assert security.is_same_site("example.com.evil.com", "example.com") is False


def test_credential_target_refused_for_evil_host():
    with pytest.raises(CredentialTargetRefused):
        security.assert_credential_target("https://evil.example.com/login", "example.test")


def test_credential_target_allows_official_and_subdomain():
    security.assert_credential_target("https://example.test/login", "example.test")
    security.assert_credential_target("https://accounts.example.test/login", "example.test")


def test_redirect_guard_blocks_evil_host():
    with pytest.raises(RedirectBlocked):
        security.assert_navigation_stays_on_site("http://evil.example.com/x", "example.test")


def test_credential_field_detection_rejects_secret_keys():
    with pytest.raises(ValueError):
        security.assert_no_secret_keys({"nested": {"password": "x"}})


# -- operations ----------------------------------------------------------------


def test_fill_requires_value():
    with pytest.raises(InvalidOperation):
        validate_operation("fill", {"selector": "#a"})


def test_upload_requires_authorization():
    with pytest.raises(InvalidOperation):
        validate_operation("upload_file", {"selector": "#f", "path": "/tmp/x"})
    validate_operation("upload_file", {"selector": "#f", "path": "/tmp/x", "authorized": True})


def test_evaluate_guarded():
    with pytest.raises(security.EvaluateRefused):
        validate_operation("evaluate", {"expression": "fetch('http://evil')"})
    with pytest.raises(security.EvaluateRefused):
        validate_operation("evaluate", {"expression": "document.title"})  # no opt-in
    assert validate_operation(
        "evaluate", {"expression": "document.title", "allow_evaluate": True})["expression"] == "document.title"


def test_navigate_rejects_bad_scheme_and_unknown_op():
    with pytest.raises(security.UnsafeUrl):
        validate_operation("navigate", {"url": "file:///etc/passwd"})
    with pytest.raises(InvalidOperation):
        validate_operation("teleport", {})


# -- detectors -----------------------------------------------------------------


def _snap(**kw):
    return PageSnapshot(**kw)


def test_detectors_pure():
    assert detect_auth_state(_snap(has_password_field=True)) == "logged_out"
    assert detect_auth_state(_snap(text="Welcome back  Log out")) == "logged_in"
    assert detect_captcha(_snap(text="please confirm you are not a robot"))["detected"] is True
    assert detect_mfa_prompt(_snap(text="enter the 6-digit verification code"))["detected"] is True
    assert detect_verification_page(_snap(text="we sent a verification link"))["detected"] is True
    assert detect_error(_snap(text="Invalid email or password"))["detected"] is True


# -- profile / session stores --------------------------------------------------


def test_profile_namespacing_and_isolation(operator):
    p1 = operator.profiles.get_or_create("ident-a", "provider")
    p2 = operator.profiles.get_or_create("ident-b", "provider")
    assert p1.dir != p2.dir
    assert operator.profiles.get_or_create("ident-a", "provider").profile_id == p1.profile_id


def test_operations_and_state_updates(operator):
    sid = operator.open_session("ident-a", "provider").session_id
    operator.perform(sid, "navigate", {"url": "https://accounts.example.test/login"})
    out = operator.perform(sid, "inspect")
    assert out["snapshot"]["has_password_field"] is True
    assert out["detectors"]["captcha"]["detected"] is False
    assert operator.get_session(sid).current_url == "https://accounts.example.test/login"
    operator.end_session(sid)


def test_takeover_state_machine(operator):
    sid = operator.open_session("ident-a", "provider", mission_id="mis-1").session_id
    operator.perform(sid, "navigate", {"url": "https://accounts.example.test/check-email"})
    record = operator.pause_for_human(
        sid, reason="verify email", action_required="EMAIL_VERIFICATION",
        resume_condition={"type": "url_contains", "value": "/dashboard"})
    assert record.status.value == "pending"
    assert operator.get_session(sid).status.value == "paused"
    assert operator.resume_if_completed(sid)["resumed"] is False

    handle = operator._handles[sid]
    handle.page.url = "https://accounts.example.test/dashboard"
    handle.page.text = "Welcome back  Log out"
    result = operator.resume_if_completed(sid)
    assert result["resumed"] is True
    assert operator.get_session(sid).status.value == "active"
    operator.end_session(sid)


def test_takeover_records_survive_store_reload(operator, tmp_path):
    sid = operator.open_session("ident-a", "provider").session_id
    record = operator.pause_for_human(sid, reason="mfa", action_required="MFA")
    reopened = BrowserOperator(tmp_path, engine=FakeEngine(), sink=RecordingSink())
    assert reopened.takeovers.get(record.takeover_id) is not None
    assert reopened.get_session(sid) is not None


def test_session_storage_state_saved_and_restored(operator):
    state = operator.open_session("ident-a", "provider")
    operator._handles[state.session_id].cookies["kai"] = "1"
    operator.end_session(state.session_id, save_state=True)
    import os

    assert os.path.exists(state.storage_state_path)
    restored = operator.open_session("ident-a", "provider", restore_storage_state=True)
    assert operator._handles[restored.session_id].cookies.get("kai") == "1"
    operator.end_session(restored.session_id)


def test_credential_domain_refusal_via_manager(operator):
    sid = operator.open_session("ident-a", "provider").session_id
    operator.perform(sid, "navigate", {"url": "https://evil.example.com/login"})
    with pytest.raises(CredentialTargetRefused):
        operator.perform(sid, "fill", {"selector": "#pw", "value": "hunter2", "credential": True})
    operator.end_session(sid)


def test_redirect_enforcement_via_manager(operator):
    sid = operator.open_session("ident-a", "provider").session_id
    with pytest.raises(RedirectBlocked):
        operator.perform(sid, "navigate", {"url": "https://accounts.example.test/redirect-evil",
                                           "enforce_domain": True})
    operator.end_session(sid)


def test_concurrency_cap(tmp_path):
    op = BrowserOperator(tmp_path, engine=FakeEngine(), sink=RecordingSink(), max_concurrent=1)
    op.set_provider_domain("provider", "accounts.example.test")
    op.open_session("ident-a", "provider")
    with pytest.raises(TooManySessions):
        op.open_session("ident-b", "provider")


def test_events_and_audit_emitted(operator):
    sid = operator.open_session("ident-a", "provider", mission_id="mis-9").session_id
    operator.perform(sid, "navigate", {"url": "https://accounts.example.test/login"})
    operator.pause_for_human(sid, reason="mfa", action_required="MFA")
    topics = operator.sink.topics()
    assert "browser.session.started" in topics
    assert "browser.session.paused" in topics
    assert "human.action.required" in topics
    audit_types = [a["event_type"] for a in operator.sink.audits]
    assert "browser.session.start" in audit_types
    assert "browser.action" in audit_types
    assert "browser.takeover.pause" in audit_types


def test_audit_never_records_credential_value(operator):
    operator.set_provider_domain("provider", "accounts.example.test")
    sid = operator.open_session("ident-a", "provider").session_id
    operator.perform(sid, "navigate", {"url": "https://accounts.example.test/login"})
    operator.perform(sid, "fill", {"selector": "#pw", "value": "s3cr3t-pw", "credential": True})
    blob = str(operator.sink.audits)
    assert "s3cr3t-pw" not in blob
    fill_records = [a for a in operator.sink.audits
                    if a["event_type"] == "browser.fill"]
    assert fill_records and fill_records[0]["details"]["value_len"] == len("s3cr3t-pw")
