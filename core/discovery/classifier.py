"""CapabilityClassifier - URL/domain (+ optional HTML/snapshot) -> SiteProfile.

Cheap, deterministic heuristics produce a baseline profile from the URL path and,
when available, the fetched HTML / browser snapshot. An optional
:class:`ReasoningBackend` may enrich that baseline. The automation policy is
**always** left ``UNKNOWN`` by the classifier -- permission is decided by the
human-confirmed policy gate in ``core.providers``, never inferred here.
"""

from __future__ import annotations

from typing import Optional

from core.discovery.reasoning import ReasoningBackend
from core.providers.schema import AutomationPolicy
from core.site_recipes.schema import (
    FlowType,
    SiteProfile,
    SiteRequirements,
    normalize_domain,
)

# --- URL path tokens -------------------------------------------------------
_SSO_TOKENS = ("sso", "oauth", "signin-with", "sign-in-with", "continue-with")
_INVITE_TOKENS = ("invite", "request-access", "waitlist", "beta")

# --- HTML markers ----------------------------------------------------------
_SSO_HTML = (
    "sign in with google", "sign in with apple", "sign in with microsoft",
    "sign in with facebook", "continue with google", "continue with apple",
    "continue with microsoft", "log in with google", "login with google",
)
_INVITE_HTML = (
    "request an invite", "request invite", "request access", "request-access",
    "join the waitlist", "join waitlist", "invite-only", "invite only",
    "request early access", "waitlist",
)
_MULTISTEP_HTML = (
    "step 1 of", "step 1/", "step 1:", "data-step", 'aria-current="step"',
    "multi-step", "signup-step", "step-one", "wizard",
)
_SIGNUP_HTML = (
    "create account", "create your account", "create an account", "sign up",
    "signup", "register", "get started",
)
_PASSWORD_HTML = (
    'type="password"', 'type=\'password\'', 'name="password"',
    "name='password'", 'autocomplete="new-password"',
)

# --- requirement markers ---------------------------------------------------
_REQUIREMENT_PATTERNS = {
    "email": ('type="email"', "name=\"email\"", "name='email'",
              "email address"),
    "phone": ('type="tel"', 'name="phone"', 'name="mobile"',
              'name="telephone"', "phone number", "mobile number"),
    "captcha": ("recaptcha", "hcaptcha", "turnstile", "captcha"),
    "mfa": ("two-factor", "two factor", "2fa", "authenticator app",
            "one-time code", "one time code", "totp"),
    "payment": ("card number", "credit card", "payment method",
                "billing address", "cc-number"),
    "kyc": ("verify your identity", "government-issued", "government issued",
            "passport", "identity verification", "upload your id"),
    "email_verify": ("verify your email", "confirm your email",
                     "email verification", "check your inbox",
                     "verification link", "verification email"),
    "phone_verify": ("verify your phone", "verify your number", "sms code",
                     "text message code", "confirm your number"),
}

_DEFAULT_POLICY_SOURCE = (
    "classifier: automation policy not yet evaluated -- human confirmation required"
)

#: Only these keys from a reasoning proposal are merged into the baseline.
_MERGEABLE_KEYS = ("signup_url", "flow_type", "requirements", "confidence",
                   "registrable", "evidence")


class CapabilityClassifier:
    """Classify a domain into a :class:`SiteProfile` without touching the net."""

    def __init__(self, backend: Optional[ReasoningBackend] = None,
                 known: Optional[dict] = None):
        self._backend = backend
        self._known = dict(known or {})

    # -- heuristics ---------------------------------------------------------
    @staticmethod
    def _flow_from_url(path: str) -> Optional[FlowType]:
        if any(token in path for token in _INVITE_TOKENS):
            return FlowType.invite_only
        if any(token in path for token in _SSO_TOKENS):
            return FlowType.sso_only
        return None

    @staticmethod
    def _flow_from_html(html: str) -> Optional[FlowType]:
        if not html:
            return None
        has_password = any(marker in html for marker in _PASSWORD_HTML)
        if any(marker in html for marker in _SSO_HTML) and not has_password:
            return FlowType.sso_only
        if any(marker in html for marker in _INVITE_HTML):
            return FlowType.invite_only
        if any(marker in html for marker in _MULTISTEP_HTML):
            return FlowType.multi_step
        if any(marker in html for marker in _SIGNUP_HTML) or "<form" in html:
            return FlowType.single_page
        return FlowType.unknown

    def _derive_flow(self, target: str, html: str) -> FlowType:
        html_flow = self._flow_from_html(html)
        if html_flow is not None and html_flow is not FlowType.unknown:
            return html_flow
        url_flow = self._flow_from_url((target or "").lower())
        if url_flow is not None:
            return url_flow
        if html_flow is FlowType.unknown:
            return FlowType.unknown
        return FlowType.single_page

    @staticmethod
    def _requirements(flow: FlowType, html: str) -> SiteRequirements:
        if not html:
            if flow is FlowType.sso_only:
                return SiteRequirements(email=True)
            return SiteRequirements(email=True, email_verify=True)
        flags = {
            field: any(marker in html for marker in markers)
            for field, markers in _REQUIREMENT_PATTERNS.items()
        }
        # A registration of any shape needs an email unless it is SSO-only.
        flags["email"] = True
        return SiteRequirements(**flags)

    def _heuristic(self, domain: str, target: str, html: str) -> SiteProfile:
        html_l = (html or "").lower()
        flow = self._derive_flow((target or "").lower(), html_l)
        return SiteProfile(
            domain=domain,
            signup_url=f"https://{domain}/signup",
            registrable=bool(domain and "." in domain),
            flow_type=flow,
            requirements=self._requirements(flow, html_l),
            automation_policy=AutomationPolicy.UNKNOWN,
            policy_source=_DEFAULT_POLICY_SOURCE,
            confidence=0.5 if html_l else 0.3,
            evidence=[f"flow:{flow.value}"]
            + (["reason:html-heuristic"] if html_l else []),
        )

    # -- reasoning enrichment ----------------------------------------------
    @staticmethod
    def _snapshot_html(snapshot: Optional[dict]) -> str:
        if not snapshot:
            return ""
        for key in ("html", "a11y_text", "text", "body"):
            value = snapshot.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _proposal_for(self, domain: str, target: str) -> Optional[dict]:
        if domain in self._known:
            known = self._known[domain]
            if hasattr(known, "model_dump"):
                return known.model_dump()
            if isinstance(known, dict):
                return dict(known)
            return None
        if self._backend is not None:
            return self._backend.propose_profile(domain, target)
        return None

    def _merge(self, profile: SiteProfile, domain: str,
               proposal: dict) -> SiteProfile:
        data = profile.model_dump()
        for key in _MERGEABLE_KEYS:
            value = proposal.get(key)
            if value is None:
                continue
            if key == "requirements" and isinstance(value, dict):
                merged = dict(data["requirements"])
                merged.update(value)
                data["requirements"] = merged
            else:
                data[key] = value
        data["domain"] = domain
        data["automation_policy"] = AutomationPolicy.UNKNOWN
        data["policy_source"] = _DEFAULT_POLICY_SOURCE
        return SiteProfile(**data)

    # -- public API ---------------------------------------------------------
    def classify(self, target: str, html: Optional[str] = None,
                 snapshot: Optional[dict] = None) -> SiteProfile:
        domain = normalize_domain(target)
        html_text = html if html is not None else self._snapshot_html(snapshot)
        profile = self._heuristic(domain, target, html_text)

        proposal = self._proposal_for(domain, target)
        if proposal:
            profile = self._merge(profile, domain, proposal)

        if not profile.policy_source:
            profile = profile.model_copy(
                update={"policy_source": _DEFAULT_POLICY_SOURCE})
        return profile


__all__ = ["CapabilityClassifier"]
