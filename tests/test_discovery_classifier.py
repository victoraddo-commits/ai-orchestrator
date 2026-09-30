"""core.discovery.classifier - URL/domain (+ HTML) -> SiteProfile heuristics."""

import socket

import pytest

from core.discovery.classifier import CapabilityClassifier
from core.discovery.reasoning import (
    FixtureReasoningBackend,
    LocalModelReasoningBackend,
)
from core.providers import (
    AutomationPolicy,
    HumanConfirmationRequired,
    ensure_automation_allowed,
    register_provider,
)
from core.providers.schema import ProviderDescriptor
from core.site_recipes.schema import FlowType


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    return tmp_path


SINGLE_PAGE_HTML = """
<html><body>
<h1>Create your account</h1>
<form action="/signup" method="post">
  <input type="email" name="email" placeholder="Email address">
  <input type="password" name="password" autocomplete="new-password">
  <button type="submit">Sign up</button>
</form>
<p>We will send a verification link. Please verify your email.</p>
</body></html>
"""

MULTI_STEP_HTML = """
<html><body>
<h1>Sign up</h1>
<form data-step="1">
  <input type="email" name="email">
  <button type="button">Next</button>
</form>
<div>Step 1 of 3</div>
</body></html>
"""

SSO_HTML = """
<html><body>
<h1>Welcome back</h1>
<a href="/auth/google">Sign in with Google</a>
<a href="/auth/apple">Sign in with Apple</a>
<form><input type="email" name="email"></form>
</body></html>
"""

INVITE_HTML = """
<html><body>
<h1>Join the waitlist</h1>
<p>This product is invite-only. Request an invite to get access.</p>
<form><input type="email" name="email"><button>Request invite</button></form>
</body></html>
"""

UNKNOWN_HTML = (
    "<html><body><h1>Documentation</h1><p>Read the docs.</p></body></html>"
)

REQUIREMENTS_HTML = """
<html><body>
<h1>Create your account</h1>
<form>
  <input type="email" name="email">
  <input type="tel" name="phone">
  <input type="password" name="password">
  <div class="g-recaptcha"></div>
  <p>Two-factor authentication is required.</p>
  <p>Enter your credit card / payment method.</p>
  <p>Verify your identity with a passport scan.</p>
  <p>We will verify your phone by SMS code.</p>
</form>
</body></html>
"""


# -- plan-specified behaviour ----------------------------------------------

def test_classifies_default_signup_shape():
    profile = CapabilityClassifier().classify("https://example.com/signup")
    assert profile.domain == "example.com"
    assert profile.flow_type.value == "single_page"
    assert profile.requirements.email is True
    assert profile.requirements.email_verify is True
    assert profile.automation_policy is AutomationPolicy.UNKNOWN
    assert profile.policy_source


def test_recognises_invite_and_sso_paths():
    invite = CapabilityClassifier().classify("https://example.com/invite/abc")
    assert invite.flow_type.value == "invite_only"
    sso = CapabilityClassifier().classify("https://example.com/sso/login")
    assert sso.flow_type.value == "sso_only"


def test_reasoning_backend_overrides_heuristics():
    backend = FixtureReasoningBackend(profiles={
        "special.example": {"flow_type": "multi_step", "confidence": 0.95,
                            "requirements": {"email": True, "phone": True}}})
    profile = CapabilityClassifier(backend=backend).classify("special.example")
    assert profile.flow_type.value == "multi_step"
    assert profile.confidence == 0.95
    assert profile.requirements.phone is True


def test_classified_unknown_policy_requires_human_confirmation(isolated):
    profile = CapabilityClassifier().classify("brand-new.example")
    register_provider(ProviderDescriptor(
        provider_id=profile.domain, display_name=profile.domain,
        official_domain=profile.domain, account_types=["web"],
        automation_policy=profile.automation_policy,
        policy_source=profile.policy_source))
    with pytest.raises(HumanConfirmationRequired) as exc:
        ensure_automation_allowed(profile.domain)
    assert exc.value.decision.automation_policy is AutomationPolicy.UNKNOWN


# -- per-shape HTML heuristics ---------------------------------------------

def test_html_single_page_heuristic():
    profile = CapabilityClassifier().classify(
        "https://shop.example/signup", html=SINGLE_PAGE_HTML)
    assert profile.flow_type is FlowType.single_page
    assert profile.requirements.email is True
    assert profile.requirements.email_verify is True
    assert profile.requirements.captcha is False


def test_html_multi_step_heuristic():
    profile = CapabilityClassifier().classify(
        "https://app.example/start", html=MULTI_STEP_HTML)
    assert profile.flow_type is FlowType.multi_step
    assert profile.requirements.email is True


def test_html_sso_heuristic():
    profile = CapabilityClassifier().classify(
        "https://app.example/login", html=SSO_HTML)
    assert profile.flow_type is FlowType.sso_only
    assert profile.requirements.email is True


def test_html_invite_heuristic():
    profile = CapabilityClassifier().classify(
        "https://app.example/home", html=INVITE_HTML)
    assert profile.flow_type is FlowType.invite_only


def test_html_unknown_heuristic():
    profile = CapabilityClassifier().classify(
        "https://app.example/blog", html=UNKNOWN_HTML)
    assert profile.flow_type is FlowType.unknown


def test_password_field_beats_sso_markers():
    html = ('<a>Sign in with Google</a>'
            '<input type="password" name="password">')
    profile = CapabilityClassifier().classify("https://app.example/join", html=html)
    assert profile.flow_type is not FlowType.sso_only


def test_requirements_detected_from_html():
    profile = CapabilityClassifier().classify(
        "https://app.example/signup", html=REQUIREMENTS_HTML)
    req = profile.requirements
    assert req.email is True
    assert req.phone is True
    assert req.captcha is True
    assert req.mfa is True
    assert req.payment is True
    assert req.kyc is True
    assert req.phone_verify is True


def test_snapshot_html_is_used_when_no_html_arg():
    profile = CapabilityClassifier().classify(
        "https://app.example/start", snapshot={"html": MULTI_STEP_HTML})
    assert profile.flow_type is FlowType.multi_step


def test_snapshot_a11y_text_is_used():
    profile = CapabilityClassifier().classify(
        "https://app.example/start",
        snapshot={"a11y_text": "Join the waitlist to request an invite"})
    assert profile.flow_type is FlowType.invite_only


# -- invariants -------------------------------------------------------------

def test_classifier_is_deterministic():
    classifier = CapabilityClassifier()
    first = classifier.classify("https://shop.example/signup", html=SINGLE_PAGE_HTML)
    second = classifier.classify("https://shop.example/signup", html=SINGLE_PAGE_HTML)
    assert first.model_dump() == second.model_dump()


def test_policy_is_always_unknown_even_from_backend():
    backend = FixtureReasoningBackend(profiles={
        "x.example": {"flow_type": "single_page",
                      "automation_policy": "ALLOWED"}})
    profile = CapabilityClassifier(backend=backend).classify("https://x.example")
    assert profile.automation_policy is AutomationPolicy.UNKNOWN
    assert "classifier" in profile.policy_source


def test_classifier_makes_no_network_calls(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("classifier must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    backend = LocalModelReasoningBackend()
    profile = CapabilityClassifier(backend=backend).classify(
        "https://shop.example/signup", html=SINGLE_PAGE_HTML)
    assert profile.domain == "shop.example"
    assert profile.automation_policy is AutomationPolicy.UNKNOWN


def test_invalid_domain_rejected():
    with pytest.raises(ValueError):
        CapabilityClassifier().classify("localhost")
