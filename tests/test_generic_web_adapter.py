"""core.providers.generic_web - universal adapter behavior (offline)."""

import json

import pytest

from core.discovery.reasoning import FixtureReasoningBackend
from core.providers import AutomationPolicy
from core.providers.adapter import ProviderAdapter
from core.providers.generic_web import GenericWebAdapter, build_generic_adapter
from core.secret_guard import SecretFieldError
from core.site_recipes import store as recipe_store
from core.site_recipes.learner import RecipeLearner
from core.site_recipes.schema import (
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


class FakeBrowser:
    def __init__(self, *, fail_selectors=()):
        self.fail = set(fail_selectors)
        self.calls = []

    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        target = params.get("selector") or params.get("url")
        self.calls.append((op, target, params.get("credential", False)))
        if target in self.fail:
            return {"op": op, "ok": False, "error": "selector_not_found"}
        if op == "inspect":
            return {"op": op, "snapshot": {"title": "Sign up", "elements": [],
                                           "has_password_field": True,
                                           "untrusted": True}}
        return {"op": op, "ok": True}


class FakeVault:
    def __init__(self):
        self.writes = {}

    def __call__(self, path, value):
        self.writes[path] = value
        return path


def _published_recipe(domain="site.example"):
    recipe = SiteRecipe(
        domain=domain, signup_url=f"https://{domain}/signup",
        status=RecipeStatus.published, source=RecipeSource.seeded,
        steps=[
            RecipeStep(index=0, action="navigate", url=f"https://{domain}/signup"),
            RecipeStep(index=1, action="fill", selector="#email",
                       value_source="identity.email"),
            RecipeStep(index=2, action="fill", selector="#password",
                       value_source="generated_password"),
            RecipeStep(index=3, action="click", selector="#submit"),
        ])
    recipe_store.save_recipe(recipe)
    return recipe_store.publish_recipe(domain)


def test_adapter_synthesizes_descriptor_from_profile():
    adapter = build_generic_adapter("site.example")
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.descriptor.provider_id == "site.example"
    assert adapter.descriptor.official_domain == "site.example"
    assert adapter.descriptor.browser_required is True
    assert adapter.descriptor.automation_policy is AutomationPolicy.UNKNOWN
    for operation in ("discovery", "registration", "verification",
                      "security_setup", "profile_setup", "account_status"):
        assert callable(getattr(adapter, operation))


def test_descriptor_surfaces_requirements_and_policy_unknown():
    profile = SiteProfile(
        domain="req.example", signup_url="https://req.example/signup",
        flow_type="sso_only",
        requirements={"email": True, "phone": True, "captcha": True, "mfa": True},
        automation_policy=AutomationPolicy.UNKNOWN,
        policy_source="classifier: test note")
    adapter = GenericWebAdapter("req.example", profile=profile, browser=FakeBrowser())

    assert adapter.descriptor.requires_email is True
    assert adapter.descriptor.requires_phone is True
    assert adapter.descriptor.requires_captcha is True
    assert adapter.descriptor.requires_mfa is True
    assert adapter.descriptor.has_oauth is True
    assert adapter.descriptor.automation_policy is AutomationPolicy.UNKNOWN
    assert adapter.descriptor.policy_source == "classifier: test note"

    surfaced = adapter.discovery()
    assert surfaced["automation_policy"] == "UNKNOWN"
    assert surfaced["flow_type"] == "sso_only"


def test_registration_drives_published_recipe_and_stores_vault_ref(isolated, monkeypatch):
    _patch_identity(monkeypatch)
    published = _published_recipe()
    browser = FakeBrowser()
    vault = FakeVault()
    adapter = GenericWebAdapter("site.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="site.example"))

    outcome = adapter.registration(browser_session_id="sess-1",
                                   identity_id="ident-1", account_id="acct-1")
    assert outcome["status"] == "submitted"
    assert outcome["requires_human"] is False
    assert outcome["recipe_version"] == published.version
    assert outcome["password_ref"] == "secrets/accounts/site_example/acct-1"
    assert set(vault.writes) == {"secrets/accounts/site_example/acct-1"}
    password = vault.writes[outcome["password_ref"]]
    assert len(password) >= 16
    assert password not in json.dumps(outcome)
    # the credential fill is flagged, and the plaintext is never in the calls list
    flagged = [c for c in browser.calls if c[0] == "fill" and c[2] is True]
    assert flagged and flagged[0][1] == "#password"
    assert all(outcome["password_ref"] != str(c[1]) for c in browser.calls)


def test_registration_without_published_recipe_learns_draft_and_pauses(isolated):
    backend = FixtureReasoningBackend(recipes={"new.example": {
        "flow_type": "single_page",
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://new.example/signup"}],
        "confidence": 0.4,
    }})
    browser = FakeBrowser()
    learner = RecipeLearner(backend, browser=browser)
    adapter = GenericWebAdapter("new.example", browser=browser, learner=learner,
                                profile=SiteProfile(domain="new.example"))

    outcome = adapter.registration(browser_session_id="sess-2")
    assert outcome["status"] == "draft_learned"
    assert outcome["requires_human"] is True

    draft = recipe_store.get_recipe("new.example")
    assert draft.status is RecipeStatus.draft
    assert draft.source is RecipeSource.learned
    assert draft.steps[0].action == "navigate"


def test_registration_marks_stale_and_relearns_on_drift(isolated, monkeypatch):
    _patch_identity(monkeypatch)
    _published_recipe("drift.example")
    backend = FixtureReasoningBackend(recipes={"drift.example": {
        "flow_type": "multi_step", "confidence": 0.5,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://drift.example/signup"}],
    }})
    browser = FakeBrowser(fail_selectors={"#email"})
    learner = RecipeLearner(backend, browser=browser)
    adapter = GenericWebAdapter("drift.example", browser=browser, learner=learner,
                                profile=SiteProfile(domain="drift.example"))

    outcome = adapter.registration(browser_session_id="sess-3",
                                   identity_id="ident-1")
    assert outcome["status"] == "drift_relearned"
    assert outcome["requires_human"] is True

    stale = recipe_store.get_recipe("drift.example", status=RecipeStatus.stale)
    assert stale is not None
    assert stale.version == 1
    assert recipe_store.get_published("drift.example") is None

    drafts = [v for v in recipe_store.read_versions("drift.example")
              if v["status"] == "draft"]
    assert drafts and drafts[-1]["source"] == "learned"


def test_learned_recipe_stores_symbolic_sources_only(isolated, tmp_path):
    backend = FixtureReasoningBackend(recipes={"sym.example": {
        "flow_type": "single_page", "confidence": 0.6,
        "steps": [
            {"index": 0, "action": "navigate", "url": "https://sym.example/signup"},
            {"index": 1, "action": "fill", "selector": "#password",
             "value_source": "generated_password"},
        ],
    }})
    browser = FakeBrowser()
    adapter = GenericWebAdapter("sym.example", browser=browser,
                                learner=RecipeLearner(backend, browser=browser),
                                profile=SiteProfile(domain="sym.example"))
    adapter.registration(browser_session_id="sess-5")

    raw = (tmp_path / "site_recipes" / "sym.example.json").read_text()
    assert "generated_password" in raw
    assert "hunter2" not in raw
    assert '"value"' not in raw


def test_learned_draft_with_secret_like_description_is_not_persisted(isolated):
    backend = FixtureReasoningBackend(recipes={"leak.example": {
        "flow_type": "single_page", "confidence": 0.6,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://leak.example/signup",
                   "description": "password=hunter2"}],
    }})
    browser = FakeBrowser()
    adapter = GenericWebAdapter("leak.example", browser=browser,
                                learner=RecipeLearner(backend, browser=browser),
                                profile=SiteProfile(domain="leak.example"))
    with pytest.raises(SecretFieldError):
        adapter.registration(browser_session_id="sess-4")
    assert recipe_store.read_versions("leak.example") == []


# ---------------------------------------------------------------------------
# I1: shared adapter instance must be stateless per mission
# ---------------------------------------------------------------------------


class _Ident:
    def __init__(self, identity_id):
        self.identity_id = identity_id
        self.display_name = identity_id
        self.email = f"{identity_id}@example.test"
        self.phone = "+10000000000"


def _patch_identity(monkeypatch):
    import core.identity as identity_mod

    monkeypatch.setattr(identity_mod, "get_identity", lambda iid: _Ident(iid))


class _InterleavingBrowser:
    """On the first credential fill it runs mission B on the SAME adapter."""

    def __init__(self):
        self.fills = []
        self._armed = None
        self._fired = False

    def arm(self, callback):
        self._armed = callback

    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        if op == "fill":
            self.fills.append((session_id, params.get("selector"),
                               params.get("value")))
            if self._armed and not self._fired and params.get("credential"):
                self._fired = True
                self._armed()
        return {"op": op, "ok": True}


def test_interleaved_missions_do_not_share_identity_or_password(monkeypatch, isolated):
    _patch_identity(monkeypatch)
    # The password step comes FIRST so mission B can overwrite any shared
    # identity slot before mission A resolves identity.email.
    recipe = SiteRecipe(
        domain="interleave.example",
        signup_url="https://interleave.example/signup",
        status=RecipeStatus.published, source=RecipeSource.seeded,
        steps=[
            RecipeStep(index=0, action="navigate",
                       url="https://interleave.example/signup"),
            RecipeStep(index=1, action="fill", selector="#password",
                       value_source="generated_password"),
            RecipeStep(index=2, action="fill", selector="#email",
                       value_source="identity.email"),
        ])
    recipe_store.save_recipe(recipe)
    recipe_store.publish_recipe("interleave.example")

    browser = _InterleavingBrowser()
    vault = FakeVault()
    adapter = GenericWebAdapter("interleave.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="interleave.example"))

    def run_mission_b():
        adapter.registration(browser_session_id="sess-B", identity_id="ident-B",
                             account_id="acct-B", mission_id="mis-B")

    browser.arm(run_mission_b)
    outcome_a = adapter.registration(browser_session_id="sess-A",
                                     identity_id="ident-A", account_id="acct-A",
                                     mission_id="mis-A")

    assert outcome_a["status"] == "submitted"
    ref_a = "secrets/accounts/interleave_example/acct-A"
    ref_b = "secrets/accounts/interleave_example/acct-B"
    assert set(vault.writes) == {ref_a, ref_b}
    assert vault.writes[ref_a] != vault.writes[ref_b]

    email_by_session = {sid: val for sid, sel, val in browser.fills
                        if sel == "#email"}
    assert email_by_session["sess-A"] == "ident-A@example.test"
    assert email_by_session["sess-B"] == "ident-B@example.test"

    pw_by_session = {sid: val for sid, sel, val in browser.fills
                     if sel == "#password"}
    assert pw_by_session["sess-A"] != pw_by_session["sess-B"]


# ---------------------------------------------------------------------------
# I2: literal vault sources + explicit-unresolved signals
# ---------------------------------------------------------------------------


class ReadWriteVault(FakeVault):
    def __init__(self, entries=None):
        super().__init__()
        self.entries = dict(entries or {})

    def read(self, path):
        return self.entries.get(path)


class _ValueBrowser:
    def __init__(self):
        self.fills = []

    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        if op == "fill":
            self.fills.append((params.get("selector"), params.get("value")))
        return {"op": op, "ok": True}


def _published_steps(domain, steps):
    recipe = SiteRecipe(domain=domain, signup_url=f"https://{domain}/signup",
                        status=RecipeStatus.published, source=RecipeSource.seeded,
                        steps=steps)
    recipe_store.save_recipe(recipe)
    return recipe_store.publish_recipe(domain)


def test_literal_value_source_reads_from_vault(isolated):
    _published_steps("literal.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://literal.example/signup"),
        RecipeStep(index=1, action="fill", selector="#token",
                   value_source="literal:secrets/accounts/literal.example/token"),
    ])
    browser = _ValueBrowser()
    vault = ReadWriteVault(
        {"secrets/accounts/literal.example/token": "token-value"})
    adapter = GenericWebAdapter("literal.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="literal.example"))

    outcome = adapter.registration(browser_session_id="sess-l1",
                                   account_id="acct-l")

    assert outcome["status"] == "submitted"
    filled = dict(browser.fills)
    assert filled["#token"] == "token-value"
    assert "token-value" not in json.dumps(outcome)


def test_literal_missing_from_vault_is_unresolved_and_pauses(isolated):
    _published_steps("noread.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://noread.example/signup"),
        RecipeStep(index=1, action="fill", selector="#token",
                   value_source="literal:secrets/accounts/noread.example/token"),
    ])
    browser = _ValueBrowser()
    adapter = GenericWebAdapter("noread.example", browser=browser,
                                vault=ReadWriteVault({}),
                                profile=SiteProfile(domain="noread.example"))

    outcome = adapter.registration(browser_session_id="sess-l2")

    assert outcome["status"] == "unresolved_value_source"
    assert outcome["requires_human"] is True
    assert browser.fills == []


def test_totp_is_explicitly_unresolved_and_pauses(isolated):
    _published_steps("totp.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://totp.example/signup"),
        RecipeStep(index=1, action="fill", selector="#otp", value_source="totp"),
    ])
    browser = _ValueBrowser()
    adapter = GenericWebAdapter("totp.example", browser=browser,
                                profile=SiteProfile(domain="totp.example"))

    outcome = adapter.registration(browser_session_id="sess-t1")

    assert outcome["status"] == "unresolved_value_source"
    assert outcome["requires_human"] is True
    assert browser.fills == []


def test_missing_identity_attribute_is_unresolved_and_pauses(isolated):
    _published_steps("noident.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://noident.example/signup"),
        RecipeStep(index=1, action="fill", selector="#email",
                   value_source="identity.email"),
    ])
    browser = _ValueBrowser()
    adapter = GenericWebAdapter("noident.example", browser=browser,
                                profile=SiteProfile(domain="noident.example"))

    outcome = adapter.registration(browser_session_id="sess-i1",
                                   identity_id="ident-does-not-exist")

    assert outcome["status"] == "unresolved_value_source"
    assert outcome["requires_human"] is True
    assert browser.fills == []


def test_generated_username_resolves_to_a_value(isolated):
    _published_steps("uname.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://uname.example/signup"),
        RecipeStep(index=1, action="fill", selector="#username",
                   value_source="generated_username"),
    ])
    browser = _ValueBrowser()
    adapter = GenericWebAdapter("uname.example", browser=browser,
                                profile=SiteProfile(domain="uname.example"))

    outcome = adapter.registration(browser_session_id="sess-u1")

    assert outcome["status"] == "submitted"
    filled = dict(browser.fills)
    assert filled.get("#username")
    assert not filled["#username"].isnumeric()


# ---------------------------------------------------------------------------
# I3: a Vault write failure must pause, not silently lose the credential
# ---------------------------------------------------------------------------


class _FlakyVault:
    def __init__(self):
        self.writes = {}
        self.fail = True

    def __call__(self, path, value):
        if self.fail:
            return None
        self.writes[path] = value
        return path


def test_vault_write_failure_pauses_and_retains_password(isolated):
    _published_steps("vaultfail.example", [
        RecipeStep(index=0, action="navigate",
                   url="https://vaultfail.example/signup"),
        RecipeStep(index=1, action="fill", selector="#password",
                   value_source="generated_password"),
    ])
    browser = _ValueBrowser()
    vault = _FlakyVault()
    adapter = GenericWebAdapter("vaultfail.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="vaultfail.example"))

    first = adapter.registration(browser_session_id="sess-v1", account_id="acct-v",
                                 mission_id="mis-v")
    assert first["status"] == "vault_write_failed"
    assert first["requires_human"] is True
    assert first.get("password_ref") is None
    assert vault.writes == {}

    # Retry (same mission) after the Vault recovers: the SAME generated
    # password is reused, proving it was not cleared on the failed write.
    vault.fail = False
    second = adapter.registration(browser_session_id="sess-v1", account_id="acct-v",
                                  mission_id="mis-v")
    ref = "secrets/accounts/vaultfail_example/acct-v"
    assert second["status"] == "submitted"
    assert second["password_ref"] == ref
    assert set(vault.writes) == {ref}

    passwords = [value for selector, value in browser.fills if selector == "#password"]
    assert len(passwords) == 2
    assert passwords[0] == passwords[1]


# ---------------------------------------------------------------------------
# I5: a published recipe with no steps must not report ``submitted``
# ---------------------------------------------------------------------------


def test_published_recipe_without_steps_pauses_without_submitting(isolated):
    _published_steps("empty.example", [])
    browser = _ValueBrowser()
    vault = FakeVault()
    adapter = GenericWebAdapter("empty.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="empty.example"))

    outcome = adapter.registration(browser_session_id="sess-e1",
                                   account_id="acct-e")

    assert outcome["status"] == "no_recipe_steps"
    assert outcome["requires_human"] is True
    assert vault.writes == {}
    assert browser.fills == []


# ---------------------------------------------------------------------------
# M2: a transient transport error must not demote a good published recipe
# ---------------------------------------------------------------------------


class _RaisingBrowser:
    def perform(self, session_id, op, params=None):
        raise TimeoutError("browser transport timeout")


def test_transient_transport_error_does_not_demote_recipe(isolated):
    _published_recipe("transient.example")
    browser = _RaisingBrowser()
    adapter = GenericWebAdapter("transient.example", browser=browser,
                                profile=SiteProfile(domain="transient.example"))

    outcome = adapter.registration(browser_session_id="sess-x")

    assert outcome["status"] == "transient_error"
    assert outcome["requires_human"] is True
    # the good recipe is NOT demoted
    assert recipe_store.get_published("transient.example") is not None
    assert recipe_store.get_recipe("transient.example", status="stale") is None


def test_selector_error_still_marks_recipe_stale(monkeypatch, isolated):
    _patch_identity(monkeypatch)
    _published_recipe("selector.example")
    browser = FakeBrowser(fail_selectors={"#email"})
    learner = RecipeLearner(
        FixtureReasoningBackend(recipes={"selector.example": {
            "flow_type": "single_page", "confidence": 0.5,
            "steps": [{"index": 0, "action": "navigate",
                       "url": "https://selector.example/signup"}],
        }}), browser=browser)
    adapter = GenericWebAdapter("selector.example", browser=browser,
                                learner=learner,
                                profile=SiteProfile(domain="selector.example"))

    outcome = adapter.registration(browser_session_id="sess-y",
                                   identity_id="ident-1")

    assert outcome["status"] == "drift_relearned"
    assert recipe_store.get_recipe("selector.example", status="stale") is not None
