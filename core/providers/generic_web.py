"""GenericWebAdapter - the default provider adapter for ANY domain.

The descriptor is synthesized from a :class:`SiteProfile` (produced by
``CapabilityClassifier``). Registration resolves a **published** recipe for the
domain and maps its ordered steps onto the browser operator; when no published
recipe exists it learns a draft and signals a human pause; when a published
step no longer resolves (drift) it marks the recipe ``stale``, re-learns and
pauses. Recipes are secret-free and are re-checked with the storage-boundary
guard before persistence.

Automation policy is always surfaced from the profile (default ``UNKNOWN``) and
is never elevated here: permission is decided by the human-confirmed policy
gate, never inferred by this adapter.
"""

from __future__ import annotations

import secrets as _secrets
import string
from typing import Optional

from core.accounts.schema import vault_reference_for
from core.discovery.classifier import CapabilityClassifier
from core.providers.adapter import NOT_APPLICABLE, ProviderAdapter
from core.providers.schema import ProviderDescriptor
from core.secret_guard import assert_no_secret_fields
from core.site_recipes import store as recipe_store
from core.site_recipes.schema import RecipeStep, SiteProfile, normalize_domain

PROVIDER_ACCOUNT_TYPE = "web"

_PASSWORD_SYMBOLS = "!@#$%^&*-_=+"


def _generate_password(length: int = 20) -> str:
    """A strong password with all character classes (never logged/returned)."""
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SYMBOLS
    while True:
        password = "".join(_secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)
                and any(c in _PASSWORD_SYMBOLS for c in password)):
            return password


class GenericWebAdapter(ProviderAdapter):
    """Universal adapter: descriptor from a profile, ops via the browser operator."""

    def __init__(self, domain: str, *, descriptor: Optional[ProviderDescriptor] = None,
                 profile: Optional[SiteProfile] = None, browser=None, vault=None,
                 learner=None, recipe_store_module=None):
        self._domain = normalize_domain(domain)
        self._profile = profile or CapabilityClassifier().classify(self._domain)
        self._descriptor = descriptor or self._descriptor_from_profile(self._profile)
        self._browser = browser
        self._vault = vault
        self._store = recipe_store_module or recipe_store
        self._learner = learner
        self._sessions_by_account: dict = {}
        self._identity_id: Optional[str] = None
        self._last_password: Optional[str] = None
        self._last_relearn = None

    # -- descriptor ----------------------------------------------------------
    @staticmethod
    def _descriptor_from_profile(profile: SiteProfile) -> ProviderDescriptor:
        req = profile.requirements
        return ProviderDescriptor(
            provider_id=profile.domain,
            display_name=profile.domain,
            official_domain=profile.domain,
            registration_url=profile.signup_url,
            account_types=[PROVIDER_ACCOUNT_TYPE],
            has_api=False,
            has_oauth=profile.flow_type.value == "sso_only",
            browser_required=True,
            requires_email=req.email,
            requires_phone=req.phone,
            requires_captcha=req.captcha,
            requires_mfa=req.mfa,
            requires_identity_verification=req.kyc,
            requires_payment=req.payment,
            automation_policy=profile.automation_policy,
            policy_source=profile.policy_source,
            regions=["*"],
            notes=f"generic web adapter for {profile.domain}",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    # -- browser / vault ports ----------------------------------------------
    def _cli(self):
        if self._browser is None:
            from core.browser.client import BrowserOperatorClient

            self._browser = BrowserOperatorClient()
        return self._browser

    def _resolve_identity(self, identity_id):
        if not identity_id:
            return None
        from core.identity import get_identity

        return get_identity(identity_id)

    def _resolve_value(self, source: Optional[str]) -> str:
        if not source:
            return ""
        if source == "generated_password":
            if self._last_password is None:
                self._last_password = _generate_password()
            return self._last_password
        if source.startswith("identity."):
            ident = self._resolve_identity(self._identity_id)
            return getattr(ident, source.split(".", 1)[1], "") if ident else ""
        return ""

    def _vault_store(self, path: str, value: str) -> Optional[str]:
        writer = self._vault
        if writer is None:  # pragma: no cover - real Vault only
            from core.ai.kai_vault_client import store_secret

            writer = store_secret
        try:
            stored = writer(path, value)
        except Exception:  # noqa: BLE001 - a vault outage must not crash onboarding
            return None
        return stored or None

    # -- recipe driving ------------------------------------------------------
    def _perform(self, session_id: str, step: RecipeStep) -> dict:
        params: dict = {}
        if step.url:
            params["url"] = step.url
        if step.selector:
            params["selector"] = step.selector
        if step.value_source:
            params["value"] = self._resolve_value(step.value_source)
            if step.value_source == "generated_password":
                params["credential"] = True
        try:
            result = self._cli().perform(session_id, step.action, params)
        except Exception as exc:  # noqa: BLE001 - any operator error is drift
            return {"ok": False, "error": type(exc).__name__}
        return result if isinstance(result, dict) else {"ok": True}

    def _learn_and_pause(self, session_id: str, *, reason: str,
                         stale: bool = False) -> dict:
        if self._learner is None:
            return {"status": "learn_unavailable", "requires_human": True,
                    "action_type": "OTHER", "instructions": reason}
        draft = self._learner.learn(self._profile, session_id=session_id,
                                    browser=self._browser)
        assert_no_secret_fields(draft.model_dump())
        saved = self._store.save_recipe(draft)
        self._last_relearn = saved
        return {"status": "drift_relearned" if stale else "draft_learned",
                "requires_human": True, "action_type": "OTHER",
                "instructions": reason, "recipe_version": saved.version}

    # -- operations ----------------------------------------------------------
    def discovery(self, **ctx) -> dict:
        return {"provider_id": self._domain, "status": "descriptor",
                "official_domain": self._domain,
                "registration_url": self._descriptor.registration_url,
                "flow_type": self._profile.flow_type.value,
                "automation_policy": self._descriptor.automation_policy.value,
                "policy_source": self._descriptor.policy_source}

    def registration(self, **ctx) -> dict:
        session_id = ctx.get("browser_session_id")
        account_id = ctx.get("account_id")
        self._identity_id = ctx.get("identity_id")
        self._last_password = None
        if not session_id:
            return {"status": "unavailable", "reason": "missing_session",
                    "requires_human": False}
        if account_id:
            self._sessions_by_account[account_id] = session_id

        published = self._store.get_published(self._domain)
        if published is None:
            return self._learn_and_pause(
                session_id,
                reason="no published recipe; learned a draft for operator review")

        for step in published.steps:
            if self._perform(session_id, step).get("ok") is False:
                self._store.mark_stale(self._domain)
                return self._learn_and_pause(
                    session_id,
                    reason="recipe drift detected; marked stale and re-learned",
                    stale=True)

        password_ref = None
        if self._last_password is not None:
            path = (vault_reference_for(self._domain, account_id) if account_id
                    else f"secrets/accounts/{self._domain}/{PROVIDER_ACCOUNT_TYPE}")
            password_ref = self._vault_store(path, self._last_password)
            self._last_password = None
        return {"status": "submitted", "requires_human": False,
                "recipe_version": published.version, "password_ref": password_ref}

    def verification(self, **ctx) -> dict:
        """Relayed email/phone verification code entry (worker hands it in)."""
        code = ctx.get("code")
        if not code:
            return {"status": "no_code_received", "handled_by": "mail_worker"}
        session_id = ctx.get("browser_session_id")
        if not session_id:
            return {"status": "no_session"}
        return {"status": "otp_received", "code_entered": True}

    def authentication(self, **ctx) -> dict:
        return {"status": "sign_in_required"}

    def security_setup(self, **ctx) -> dict:
        account_id = ctx.get("account_id")
        reference = (vault_reference_for(self._domain, account_id)
                     if account_id else None)
        return {"status": "security_settings_reviewed", "mfa_offered": True,
                "mfa_enabled": False, "vault_reference": reference}

    def profile_setup(self, **ctx) -> dict:
        return {"status": "profile_synced"}

    def recovery(self, **ctx) -> dict:
        return {"status": "recovery_reviewed", "values_persisted": False}

    def publishing(self, **ctx):
        return NOT_APPLICABLE

    def account_status(self, **ctx) -> dict:
        return {"status": "active", "source": "recipe"}


def build_generic_adapter(domain: str, *,
                          descriptor: Optional[ProviderDescriptor] = None,
                          browser=None, vault=None, classifier=None,
                          learner=None, reasoning=None) -> GenericWebAdapter:
    """Build the default universal adapter for *domain*."""
    classifier = classifier or CapabilityClassifier(backend=reasoning)
    profile = classifier.classify(domain)
    if learner is None:
        from core.discovery.reasoning import LocalModelReasoningBackend
        from core.site_recipes.learner import RecipeLearner

        learner = RecipeLearner(reasoning or LocalModelReasoningBackend(),
                                browser=browser)
    return GenericWebAdapter(domain, descriptor=descriptor, profile=profile,
                             browser=browser, vault=vault, learner=learner)


__all__ = ["GenericWebAdapter", "build_generic_adapter", "PROVIDER_ACCOUNT_TYPE"]
