"""Offline site-shape fixtures + a fake browser for universal-web tests.

Each shape is described by:

* an HTML file under ``tests/fixtures/sites/`` (the reference markup for the
  page graph — loaded, never fetched);
* a named-page graph (URL, accessible text, element refs, the page reached on
  click) plus a seeded/published Site Recipe.

:class:`FakeSiteBrowser` replays those pages deterministically. Nothing here
touches a network, a real browser, mail, SMS or the Vault.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

SITES_DIR = Path(__file__).parent / "fixtures" / "sites"

_VERIFIED_PAGE = {
    "url": "https://__verified__.example/verify",
    "title": "Email verified",
    "text": "Your email has been verified. Welcome!",
    "elements": [],
    "has_password_field": False,
}


#: The three distinct signup shapes. HTML lives in the paired ``html_file``.
SHAPES: dict[str, dict] = {
    "single_page": {
        "domain": "singlepage.example",
        "signup_url": "https://singlepage.example/signup",
        "html_file": "single_page.html",
        "start": "signup",
        "requirements": {"email": True, "email_verify": True},
        "mail_fixture": "verification_singlepage.eml",
        "pages": {
            "signup": {
                "url": "https://singlepage.example/signup",
                "title": "Create your account",
                "text": ("Create your account. Sign up for an account. Full name "
                         "Email Password Create account"),
                "has_password_field": True,
                "elements": [
                    {"role": "textbox", "name": "Full name", "selector": "#name"},
                    {"role": "textbox", "name": "Email", "selector": "#email"},
                    {"role": "textbox", "name": "Password", "type": "password",
                     "selector": "#password"},
                ],
                "on_click": "done",
            },
            "done": {
                "url": "https://singlepage.example/welcome",
                "title": "Welcome",
                "text": "Your account is ready. You are signed in. Sign out",
                "has_password_field": False,
                "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}],
                "on_click": None,
            },
        },
        "recipe": {
            "flow_type": "single_page",
            "requirements": {"email": True, "email_verify": True},
            "fields": [
                {"name": "Full name", "selector": "#name", "kind": "text",
                 "value_source": "identity.display_name", "step": 0},
                {"name": "Email", "selector": "#email", "kind": "email",
                 "value_source": "identity.email", "step": 0},
                {"name": "Password", "selector": "#password", "kind": "password",
                 "value_source": "generated_password", "step": 0},
            ],
            "steps": [
                {"index": 0, "action": "navigate",
                 "url": "https://singlepage.example/signup"},
                {"index": 1, "action": "fill", "selector": "#name",
                 "value_source": "identity.display_name"},
                {"index": 2, "action": "fill", "selector": "#email",
                 "value_source": "identity.email"},
                {"index": 3, "action": "fill", "selector": "#password",
                 "value_source": "generated_password"},
                {"index": 4, "action": "click", "selector": "#submit"},
            ],
            "verification_flow": "email_link",
            "confidence": 0.9,
        },
    },
    "multi_step": {
        "domain": "wizard.example",
        "signup_url": "https://wizard.example/register",
        "html_file": "multi_step.html",
        "start": "step1",
        "requirements": {"email": True, "phone": True,
                         "email_verify": True, "phone_verify": True},
        "mail_fixture": "verification_wizard.eml",
        "pages": {
            "step1": {
                "url": "https://wizard.example/register",
                "title": "Step 1 of 2",
                "text": "Step 1 of 2. Create an account. Email. Continue",
                "has_password_field": False,
                "elements": [{"role": "textbox", "name": "Email", "selector": "#email"}],
                "on_click": "step2",
            },
            "step2": {
                "url": "https://wizard.example/register/2",
                "title": "Step 2 of 2",
                "text": "Step 2 of 2. Password. Mobile number. Finish",
                "has_password_field": True,
                "elements": [
                    {"role": "textbox", "name": "Password", "type": "password",
                     "selector": "#password"},
                    {"role": "textbox", "name": "Mobile number", "selector": "#phone"},
                ],
                "on_click": "done",
            },
            "done": {
                "url": "https://wizard.example/welcome",
                "title": "All set",
                "text": "Account created. You are signed in. Sign out",
                "has_password_field": False,
                "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}],
                "on_click": None,
            },
        },
        "recipe": {
            "flow_type": "multi_step",
            "requirements": {"email": True, "phone": True,
                             "email_verify": True, "phone_verify": True},
            "fields": [
                {"name": "Email", "selector": "#email", "kind": "email",
                 "value_source": "identity.email", "step": 0},
                {"name": "Password", "selector": "#password", "kind": "password",
                 "value_source": "generated_password", "step": 1},
                {"name": "Mobile number", "selector": "#phone", "kind": "phone",
                 "value_source": "identity.phone", "step": 1},
            ],
            "steps": [
                {"index": 0, "action": "navigate",
                 "url": "https://wizard.example/register"},
                {"index": 1, "action": "fill", "selector": "#email",
                 "value_source": "identity.email"},
                {"index": 2, "action": "click", "selector": "#next"},
                {"index": 3, "action": "fill", "selector": "#password",
                 "value_source": "generated_password"},
                {"index": 4, "action": "fill", "selector": "#phone",
                 "value_source": "identity.phone"},
                {"index": 5, "action": "click", "selector": "#finish"},
            ],
            "verification_flow": "email_link+otp",
            "confidence": 0.85,
        },
    },
    "sso_invite_only": {
        "domain": "invite.example",
        "signup_url": "https://invite.example/invite",
        "html_file": "sso_invite_only.html",
        "start": "invite",
        "requirements": {"email": True, "email_verify": True},
        "mail_fixture": None,
        "pages": {
            "invite": {
                "url": "https://invite.example/invite",
                "title": "Request an invitation",
                "text": ("This product is invite-only. Sign in with Google. Continue "
                         "with Apple. Request access to join the waitlist. Enter the "
                         "email your invitation was sent to. Continue"),
                "has_password_field": False,
                "elements": [
                    {"role": "textbox", "name": "Invite email",
                     "selector": "#invite-email"},
                ],
                "on_click": "done",
            },
            "done": {
                "url": "https://invite.example/welcome",
                "title": "Welcome",
                "text": "Your invitation was accepted. You are signed in. Sign out",
                "has_password_field": False,
                "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}],
                "on_click": None,
            },
        },
        "recipe": {
            "flow_type": "invite_only",
            "requirements": {"email": True, "email_verify": True},
            "fields": [
                {"name": "Invite email", "selector": "#invite-email", "kind": "email",
                 "value_source": "identity.email", "step": 0},
            ],
            "steps": [
                {"index": 0, "action": "navigate", "url": "https://invite.example/invite"},
                {"index": 1, "action": "fill", "selector": "#invite-email",
                 "value_source": "identity.email"},
                {"index": 2, "action": "click", "selector": "#continue"},
            ],
            "verification_flow": "email_link",
            "confidence": 0.8,
        },
    },
}


def load_site(name: str) -> dict:
    """Return a deep copy of a shape with its reference HTML loaded from disk."""
    site = copy.deepcopy(SHAPES[name])
    site["html"] = (SITES_DIR / site["html_file"]).read_text(encoding="utf-8")
    assert site["html"].strip(), f"empty HTML fixture for {name}"
    return site


class FakeVault:
    """In-memory stand-in for the Vault machine plane (write -> reference)."""

    def __init__(self):
        self.writes: dict[str, str] = {}

    def __call__(self, path: str, value: str) -> str:
        self.writes[path] = value
        return path


class FakeSiteBrowser:
    """Deterministic, resumable, offline stand-in for the CT110 operator."""

    def __init__(self, site: dict, *, resume_after: int = 1):
        self.site = site
        self.sessions: dict = {}
        self.calls: list = []
        self.fills: list = []
        self.failing_selectors: set = set()
        self.resume_after = resume_after
        self._seq = 0

    # -- profiles / sessions -------------------------------------------------
    def create_profile(self, identity_id, provider_id):
        self.calls.append(("create_profile", identity_id, provider_id))
        return {"profile_id": f"prof-{identity_id}-{provider_id}"}

    def open_session(self, identity_id, provider_id, mission_id=None, headless=None):
        self._seq += 1
        sid = f"sess-{self.site['domain']}-{self._seq}"
        self.sessions[sid] = {
            "identity_id": identity_id, "provider_id": provider_id,
            "mission_id": mission_id, "page": self.site["start"], "resumed": 0,
        }
        self.calls.append(("open_session", sid))
        return {"session_id": sid, "identity_id": identity_id,
                "provider_id": provider_id, "mission_id": mission_id}

    # -- page helpers --------------------------------------------------------
    def _state(self, session_id):
        return self.sessions.setdefault(session_id, {
            "identity_id": None, "provider_id": None, "mission_id": None,
            "page": self.site["start"], "resumed": 0})

    def _page(self, st):
        if st.get("page") == "__verified__":
            return _VERIFIED_PAGE
        return self.site["pages"].get(st["page"]) or _VERIFIED_PAGE

    def _page_for(self, st, url):
        for name, page in self.site["pages"].items():
            if page["url"] == url:
                st["page"] = name
                return page
        return self._page(st)

    @staticmethod
    def _snap(page):
        return {"url": page["url"], "title": page["title"],
                "text": page.get("text", ""), "elements": page.get("elements", []),
                "has_password_field": page.get("has_password_field", False),
                "untrusted": True}

    # -- operations ----------------------------------------------------------
    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        st = self._state(session_id)
        target = params.get("url") or params.get("selector")
        self.calls.append((op, target))
        if op == "navigate":
            if "verify" in str(params.get("url")).lower():
                st["page"] = "__verified__"
                page = _VERIFIED_PAGE
            else:
                page = self._page_for(st, params.get("url"))
            return {"op": op, "snapshot": self._snap(page)}
        if op == "inspect":
            return {"op": op, "snapshot": self._snap(self._page(st))}
        if op in ("fill", "click"):
            selector = params.get("selector")
            if selector in self.failing_selectors:
                return {"op": op, "ok": False, "error": "selector_not_found"}
            if op == "fill":
                self.fills.append((selector, params.get("value"),
                                   bool(params.get("credential"))))
            else:
                nxt = self._page(st).get("on_click")
                if nxt:
                    st["page"] = nxt
            return {"op": op, "ok": True, "selector": selector}
        return {"op": op, "ok": True}

    # -- human takeover ------------------------------------------------------
    def pause_for_human(self, session_id, reason="", action_required="", **kwargs):
        st = self._state(session_id)
        self.calls.append(("pause", action_required))
        return {"takeover_id": f"take-{session_id}", "session_id": session_id,
                "mission_id": st.get("mission_id"), "provider_id": st.get("provider_id"),
                "action_required": action_required, "instructions": reason,
                "no_vnc_url": None}

    def resume(self, session_id):
        st = self._state(session_id)
        st["resumed"] = st.get("resumed", 0) + 1
        return {"resumed": st["resumed"] >= self.resume_after,
                "takeover_id": f"take-{session_id}"}

    def end_session(self, session_id):
        self.calls.append(("end_session", session_id))
        return {"session_id": session_id, "status": "ended"}


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def descriptor_from_site(site: dict):
    from core.providers import AutomationPolicy, ProviderDescriptor

    req = site["requirements"]
    return ProviderDescriptor(
        provider_id=site["domain"], display_name=site["domain"],
        official_domain=site["domain"], registration_url=site["signup_url"],
        account_types=["web"], browser_required=True,
        requires_email=req.get("email", False),
        requires_phone=req.get("phone", False),
        requires_captcha=req.get("captcha", False),
        requires_mfa=req.get("mfa", False),
        requires_identity_verification=req.get("kyc", False),
        requires_payment=req.get("payment", False),
        automation_policy=AutomationPolicy.ALLOWED,
        policy_source="fixture: operator-confirmed offline simulation",
        regions=["*"])


def recipe_from_site(site: dict, *, flow_type=None, status="published", source="seeded"):
    from core.site_recipes.schema import RecipeSource, RecipeStatus, SiteRecipe

    data = copy.deepcopy(site["recipe"])
    confidence = float(data.pop("confidence", 0.9))
    if flow_type is not None:
        data["flow_type"] = flow_type
    return SiteRecipe(domain=site["domain"], signup_url=site["signup_url"],
                      status=RecipeStatus(status), source=RecipeSource(source),
                      confidence=confidence, **data)


def reasoning_backend(site: dict):
    from core.discovery.reasoning import FixtureReasoningBackend

    return FixtureReasoningBackend(recipes={site["domain"]: copy.deepcopy(site["recipe"])})


SMS_FIXTURE = Path(__file__).parent / "fixtures" / "sms" / "inbound.json"
MAIL_FIXTURES = Path(__file__).parent / "fixtures" / "mail"


def make_identity(*, email="kai-e2e@example.invalid", phone="+233200000001"):
    from core.identity import IdentityCreate, create_identity, set_default_identity

    ident = create_identity(IdentityCreate(display_name="KAI E2E", email=email, phone=phone))
    set_default_identity(ident.identity_id)
    return ident


def trust_mail_domain(monkeypatch, site: dict):
    """Make the mail parser recognise the fixture domain as its own provider.

    In production a provider's mail account is registered with its
    ``official_domains``; here we equivalent-register the offline fixture domain
    so the *real* mail worker validates sender alignment without a network.
    """
    from core.mail import parser

    monkeypatch.setitem(parser.PROVIDER_DOMAINS, site["domain"], site["domain"])


def copy_mailbox(tmp_path, name=None):
    import shutil

    box = Path(tmp_path) / "mailbox"
    box.mkdir(exist_ok=True)
    if name:
        shutil.copy(MAIL_FIXTURES / name, box / name)
    return box


def sms_consumer(record, descriptor):
    """The real SMS worker over the offline inbound fixture (no phone, no net)."""
    from core.sms import manager as sms_manager
    from core.sms.adapter import RawSms

    payload = json.loads(SMS_FIXTURE.read_text())[0]
    sms_manager.ingest_raw(
        RawSms(from_number=payload["from"], to_number=payload["to"], body=payload["body"]),
        mission_id=record["mission_id"], account_id=record.get("account_id"))
    code = sms_manager.consume_otp_for(
        mission_id=record["mission_id"], account_id=record.get("account_id"))
    return {"otp_present": code is not None, "verified": code is not None}


def make_pipeline(browser, box):
    from core.mail.transports import FixtureTransport
    from core.onboarding.manager import default_pipeline

    return default_pipeline(client=browser, mail_transport=FixtureTransport(box),
                            sms_consumer=sms_consumer)


def make_adapter(site: dict, *, browser, vault, flow_type=None):
    from core.providers.generic_web import GenericWebAdapter
    from core.site_recipes.learner import RecipeLearner
    from core.site_recipes.schema import FlowType, SiteProfile, SiteRequirements

    recipe = site["recipe"]
    profile = SiteProfile(
        domain=site["domain"], signup_url=site["signup_url"],
        flow_type=FlowType(flow_type or recipe["flow_type"]),
        requirements=SiteRequirements(**recipe["requirements"]))
    learner = RecipeLearner(reasoning_backend(site), browser=browser)
    return GenericWebAdapter(site["domain"], descriptor=descriptor_from_site(site),
                             profile=profile, browser=browser, vault=vault,
                             learner=learner)


__all__ = [
    "SHAPES",
    "SITES_DIR",
    "SMS_FIXTURE",
    "MAIL_FIXTURES",
    "load_site",
    "FakeVault",
    "FakeSiteBrowser",
    "descriptor_from_site",
    "recipe_from_site",
    "reasoning_backend",
    "make_adapter",
    "make_identity",
    "trust_mail_domain",
    "copy_mailbox",
    "sms_consumer",
    "make_pipeline",
]
