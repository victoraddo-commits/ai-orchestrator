"""core.site_recipes.learner - DOM reasoning -> draft recipe."""

import pytest
from pydantic import ValidationError

from core.discovery.reasoning import FixtureReasoningBackend
from core.site_recipes.learner import RecipeLearner
from core.site_recipes.schema import (
    FlowType,
    RecipeSource,
    RecipeStatus,
    SiteProfile,
)


class FakeInspectBrowser:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def perform(self, session_id, op, params=None):
        self.calls.append(op)
        return {"op": op, "snapshot": self.snapshot}


def _profile():
    return SiteProfile(domain="learn.example",
                       signup_url="https://learn.example/signup")


def test_learn_produces_draft_from_backend_proposal():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page",
        "steps": [
            {"index": 0, "action": "navigate", "url": "https://learn.example/signup"},
            {"index": 1, "action": "fill", "selector": "#email",
             "value_source": "identity.email"},
            {"index": 2, "action": "click", "selector": "#submit"},
        ],
        "fields": [{"name": "Email", "selector": "#email", "kind": "email",
                    "value_source": "identity.email"}],
        "verification_flow": "email_link",
        "confidence": 0.7,
    }})
    browser = FakeInspectBrowser({"url": "https://learn.example/signup",
                                  "title": "Sign up", "elements": [],
                                  "has_password_field": True, "untrusted": True})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                           session_id="s1")

    assert recipe.domain == "learn.example"
    assert recipe.source is RecipeSource.learned
    assert recipe.status is RecipeStatus.draft
    assert recipe.flow_type is FlowType.single_page
    assert recipe.version == 1
    assert recipe.confidence == 0.7
    assert [s.action for s in recipe.steps] == ["navigate", "fill", "click"]
    assert recipe.fields[0].value_source == "identity.email"
    assert recipe.verification_flow == "email_link"
    assert browser.calls == ["inspect"]


def test_learn_without_backend_proposal_yields_empty_draft():
    backend = FixtureReasoningBackend()
    browser = FakeInspectBrowser({"title": "", "elements": []})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                           session_id="s1")
    assert recipe.steps == []
    assert recipe.fields == []
    assert recipe.source is RecipeSource.learned
    assert recipe.status is RecipeStatus.draft
    assert recipe.confidence == 0.0


def test_learn_low_confidence_returns_draft_not_published():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "multi_step", "confidence": 0.2,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://learn.example/signup"}],
    }})
    browser = FakeInspectBrowser({"title": "Sign up", "elements": []})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                           session_id="s1")
    assert recipe.status is RecipeStatus.draft
    assert recipe.confidence == 0.2
    assert recipe.flow_type is FlowType.multi_step


def test_learn_without_session_does_not_call_browser():
    backend = FixtureReasoningBackend()
    browser = FakeInspectBrowser({"title": "x", "elements": []})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile())
    assert recipe.status is RecipeStatus.draft
    assert browser.calls == []


def test_learn_rejects_literal_secret_value_source():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page",
        "steps": [{"index": 0, "action": "fill", "selector": "#password",
                   "value_source": "hunter2"}],
        "confidence": 0.9,
    }})
    browser = FakeInspectBrowser({"title": "Sign up", "elements": []})
    with pytest.raises(ValidationError):
        RecipeLearner(backend, browser=browser).learn(_profile(),
                                                      session_id="s1")


def test_learn_works_without_a_browser():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page", "confidence": 0.5,
    }})
    recipe = RecipeLearner(backend).learn(_profile(), session_id="s1")
    assert recipe.status is RecipeStatus.draft
    assert recipe.confidence == 0.5
    assert recipe.steps == []


# ---------------------------------------------------------------------------
# I4: an untrusted backend proposal must never escape as a ValidationError
# ---------------------------------------------------------------------------


def test_learn_malformed_proposal_yields_empty_draft():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page",
        "steps": [{"selector": "#email"}],   # missing required action
        "fields": [{"name": "Email"}],        # missing required selector
        "confidence": 0.9,
    }})
    browser = FakeInspectBrowser({"title": "Sign up", "elements": []})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                           session_id="s1")
    assert recipe.steps == []
    assert recipe.fields == []
    assert recipe.status is RecipeStatus.draft
    assert recipe.source is RecipeSource.learned


def test_learn_filters_unknown_proposal_keys():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page",
        "bogus_top_level": 1,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://learn.example/signup", "bogus": "x"}],
        "confidence": 0.5,
    }})
    recipe = RecipeLearner(backend).learn(_profile(), session_id="s1")
    assert [s.action for s in recipe.steps] == ["navigate"]


def test_learn_invalid_flow_type_falls_back_to_profile():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "not-a-real-flow", "confidence": 0.5,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://learn.example/signup"}],
    }})
    recipe = RecipeLearner(backend).learn(_profile(), session_id="s1")
    assert recipe.flow_type is FlowType.unknown   # _profile() default
    assert [s.action for s in recipe.steps] == ["navigate"]


def test_learn_non_dict_proposal_yields_empty_draft():
    backend = FixtureReasoningBackend(recipes={"learn.example": ["not", "a", "dict"]})
    recipe = RecipeLearner(backend).learn(_profile(), session_id="s1")
    assert recipe.steps == [] and recipe.fields == []
    assert recipe.status is RecipeStatus.draft
