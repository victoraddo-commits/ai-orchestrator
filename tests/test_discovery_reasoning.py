"""core.discovery.reasoning - protocol + fixture + local backends."""

import socket

from core.discovery.reasoning import (
    DEFAULT_MODEL,
    FixtureReasoningBackend,
    LocalModelReasoningBackend,
    ReasoningBackend,
)
from core.site_recipes.schema import SiteProfile


def test_fixture_backend_returns_registered_profile():
    backend = FixtureReasoningBackend(
        profiles={"x.example": {"flow_type": "sso_only", "confidence": 0.8}})
    assert backend.propose_profile("x.example", "https://x.example") == {
        "flow_type": "sso_only", "confidence": 0.8}
    assert backend.propose_profile("y.example", "https://y.example") is None


def test_fixture_backend_returns_registered_recipe():
    backend = FixtureReasoningBackend(
        recipes={"x.example": {"steps": [{"index": 0, "action": "navigate",
                                          "url": "https://x.example/signup"}]}})
    proposal = backend.propose_recipe(SiteProfile(domain="x.example"),
                                      {"title": "Sign up"})
    assert proposal["steps"][0]["action"] == "navigate"


def test_fixture_backend_recipe_missing_domain_is_none():
    assert FixtureReasoningBackend().propose_recipe(
        SiteProfile(domain="nope.example"), {}) is None


def test_local_model_backend_returns_none_until_enabled():
    backend = LocalModelReasoningBackend()
    assert backend.enabled is False
    assert backend.propose_profile("x.example", "https://x.example") is None
    assert backend.propose_recipe(SiteProfile(domain="x.example"), {}) is None


def test_fixture_backend_satisfies_protocol():
    assert isinstance(FixtureReasoningBackend(), ReasoningBackend)


def test_local_model_defaults_to_local_fabric_model():
    backend = LocalModelReasoningBackend()
    assert backend.model == DEFAULT_MODEL == "qwen3-coder:kai"


def test_local_model_disabled_does_no_network(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("network access attempted while disabled")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    backend = LocalModelReasoningBackend()
    assert backend.propose_profile("x.example", "https://x.example") is None
    assert backend.propose_recipe(SiteProfile(domain="x.example"), {}) is None


def test_local_model_enabled_uses_injected_delegate():
    seen = {}

    def _delegate(description, **kwargs):
        seen["description"] = description
        seen["kwargs"] = kwargs
        return '{"flow_type": "multi_step", "confidence": 0.7}'

    backend = LocalModelReasoningBackend(enabled=True, delegate=_delegate)
    proposal = backend.propose_profile("x.example", "https://x.example")
    assert proposal == {"flow_type": "multi_step", "confidence": 0.7}
    assert "x.example" in seen["description"]
    assert seen["kwargs"]["task_type"] == "text_task"


def test_local_model_enabled_parses_fenced_json():
    def _delegate(description, **kwargs):
        return "Sure!\n```json\n{\"flow_type\": \"unknown\"}\n```\n"

    backend = LocalModelReasoningBackend(enabled=True, delegate=_delegate)
    assert backend.propose_profile("x.example", "https://x.example") == {
        "flow_type": "unknown"}


def test_local_model_enabled_bad_json_returns_none():
    backend = LocalModelReasoningBackend(
        enabled=True, delegate=lambda description, **kw: "not json")
    assert backend.propose_profile("x.example", "https://x.example") is None


def test_local_model_enabled_delegate_error_returns_none():
    def _delegate(description, **kwargs):
        raise RuntimeError("fabric down")

    backend = LocalModelReasoningBackend(enabled=True, delegate=_delegate)
    assert backend.propose_profile("x.example", "https://x.example") is None
