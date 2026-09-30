"""Amazon provider adapter — the first *real* provider integration (STEP 7).

This adapter conforms to :class:`core.providers.adapter.ProviderAdapter` and is
wired into the Universal Onboarding state machine: every call goes through the
ToS/legality gate first (``get_ready_adapter``). Because Amazon's automation
policy is recorded as **UNKNOWN** (see the descriptor's ``policy_source``), the
gate raises :class:`HumanConfirmationRequired` before any automated step — the
correct, safe default. A human must confirm (a confirmed policy override)
before KAI will drive a browser on Amazon.

Scope / honesty
---------------
* **No live Amazon run.** The adapter is driven against offline fixtures in the
  test-suite; a real run requires the operator's explicit authorization *and*
  their identity data (a gated human step). The adapter never fabricates
  identity/business/financial data.
* **No credential value is ever returned or logged.** The generated password is
  written to the Vault at the account's ``vault_reference`` path; only that
  *path* leaves this module.
* **Human takeover is owned by the engine.** The adapter *detects* blocking
  states (CAPTCHA / OTP / ToS) during ``registration`` and reports them; the
  onboarding engine performs the actual ``pause_for_human`` via its
  ``CAPTCHA/HUMAN_TAKEOVER`` / ``IDENTITY_VERIFICATION`` states. This avoids
  creating two takeovers for one blocking page and keeps pause/resume resumable
  (the paused session is the one the browser opened for the mission).

Account types: ``customer`` (default), ``business``, ``seller``, ``associates``.
Seller/business onboarding additionally requires identity verification, which is
always a human step.
"""

from __future__ import annotations

import secrets as _secrets
import string
from typing import Optional

from core.accounts.schema import vault_reference_for
from core.browser import security as browser_security
from core.browser.detectors import detect_auth_state, detect_captcha
from core.providers import store
from core.providers.adapter import NOT_APPLICABLE, ProviderAdapter
from core.providers.schema import ProviderDescriptor

PROVIDER_ID = "amazon"

#: In priority order; ``customer`` is the default.
ACCOUNT_TYPES = ("customer", "business", "seller", "associates")

#: Per-account-type registration entry points.
REGISTRATION_URLS = {
    "customer": "https://www.amazon.com/ap/register",
    "business": "https://www.amazon.com/business/register",
    "seller": "https://sellercentral.amazon.com/registration",
    "associates": "https://affiliate-program.amazon.com/join",
}

SIGNIN_URL = "https://www.amazon.com/ap/signin"
SECURITY_URL = "https://www.amazon.com/ax/account/security"

#: Account types that require identity verification (always a human step).
_IDENTITY_REQUIRED_TYPES = ("seller", "business")

#: Objective keywords → account type. ``customer`` is the fallback.
_ACCOUNT_TYPE_KEYWORDS = (
    ("seller", ("seller", "sell on amazon", "seller central", "fba",
                "fulfilment by amazon", "merchant")),
    ("business", ("amazon business", "business account", "b2b", "wholesale",
                  "procurement")),
    ("associates", ("associates", "affiliate", "referral program")),
)

#: Page text that indicates the account now exists / the session is signed in.
_CREATED_MARKERS = (
    "account created", "account was created", "your account is ready",
    "welcome to amazon", "you are signed in", "hello,", "sign out",
)

_PASSWORD_SYMBOLS = "!@#$%^&*-_=+"


def resolve_account_type(objective: str = "", ctx: Optional[dict] = None) -> str:
    """Resolve the Amazon account type from an objective/context. Default customer."""
    parts = [str(objective or "")]
    if isinstance(ctx, dict):
        for key in ("account_type", "context", "objective", "provider_objective",
                    "notes", "reason"):
            value = ctx.get(key)
            if value:
                parts.append(str(value))
    haystack = " ".join(parts).lower()
    for account_type, keywords in _ACCOUNT_TYPE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return account_type
    return "customer"


def _generate_password(length: int = 20) -> str:
    """A strong password with all character classes (never logged/returned)."""
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SYMBOLS
    while True:
        password = "".join(_secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)
                and any(c in _PASSWORD_SYMBOLS for c in password)):
            return password


def _as_snapshot(snapshot) -> dict:
    if hasattr(snapshot, "model_dump"):
        snapshot = snapshot.model_dump()
    return dict(snapshot or {})


def _detect_account_created(snapshot) -> bool:
    snap = _as_snapshot(snapshot)
    haystack = " ".join(str(snap.get(k) or "") for k in ("title", "text", "url")).lower()
    if any(marker in haystack for marker in _CREATED_MARKERS):
        return True
    return detect_auth_state(snap) == "logged_in"


class AmazonAdapter(ProviderAdapter):
    """Real Amazon adapter (browser + email/SMS + human takeover)."""

    def __init__(
        self,
        descriptor: Optional[ProviderDescriptor] = None,
        *,
        account_type: str = "customer",
        browser=None,
        vault=None,
        identity_resolver=None,
    ):
        if descriptor is not None:
            self._base = descriptor
            self._descriptor = descriptor
        else:
            self._base = store.get_base(PROVIDER_ID)
            if self._base is None:
                raise store.ProviderNotFound(PROVIDER_ID)
            self._descriptor = self.descriptor_for(account_type, self._base)
        self._account_type_default = (
            account_type if account_type in ACCOUNT_TYPES else "customer")
        self._browser = browser
        self._vault = vault
        self._identity_resolver = identity_resolver
        # account_id / session_id -> the browser session this adapter drives
        self._sessions_by_account: dict[str, str] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    # -- descriptor helpers --------------------------------------------------
    @staticmethod
    def descriptor_for(account_type: str = "customer",
                       base: Optional[ProviderDescriptor] = None) -> ProviderDescriptor:
        """The descriptor variant for *account_type* (customer is the base)."""
        atype = account_type if account_type in ACCOUNT_TYPES else "customer"
        base = base or store.get_base(PROVIDER_ID)
        if base is None:
            raise store.ProviderNotFound(PROVIDER_ID)
        return base.model_copy(update={
            "registration_url": REGISTRATION_URLS[atype],
            "requires_identity_verification": atype in _IDENTITY_REQUIRED_TYPES,
        })

    def _official_domains(self) -> list[str]:
        base = self._base or self._descriptor
        domains: list[str] = []
        if base and base.official_domain:
            domains.append(base.official_domain)
        for region in (getattr(base, "regions", None) or []):
            if region and region not in domains:
                domains.append(region)
        return domains or ["amazon.com"]

    # -- injected ports ------------------------------------------------------
    def _cli(self):
        if self._browser is None:
            from core.browser.client import BrowserOperatorClient

            self._browser = BrowserOperatorClient()
        return self._browser

    def _perform(self, session_id, op, params=None) -> dict:
        return self._cli().perform(session_id, op, params or {})

    def _inspect(self, session_id) -> dict:
        result = self._perform(session_id, "inspect", {})
        if isinstance(result, dict) and "snapshot" in result:
            return result["snapshot"] or {}
        return result or {}

    def _assert_official_host(self, url: str) -> None:
        host = browser_security.host_of(url or "")
        domains = self._official_domains()
        if not host or not any(browser_security.is_same_site(host, d) for d in domains):
            raise browser_security.CredentialTargetRefused(
                f"amazon registration host not official: {host!r} not in {domains!r}")

    def _vault_store(self, path: str, value: str) -> Optional[str]:
        """Write the secret via the injected writer (or the real Vault), ref only."""
        writer = self._vault
        if writer is None:  # pragma: no cover - exercised only with the real Vault
            from core.ai.kai_vault_client import store_secret

            writer = store_secret
        try:
            stored = writer(path, value)
        except Exception:  # noqa: BLE001 - a vault outage must not crash onboarding
            return None
        return stored or None

    def _resolve_identity(self, identity_id):
        if self._identity_resolver is not None:
            return self._identity_resolver(identity_id)
        if not identity_id:
            return None
        from core.identity import get_identity

        return get_identity(identity_id)

    def _account_type_from_ctx(self, ctx: dict) -> str:
        objective = ctx.get("objective") or ctx.get("context") or ""
        if objective:
            return resolve_account_type(objective, ctx)
        return self._account_type_default

    def _session_for(self, ctx: dict) -> Optional[str]:
        session_id = ctx.get("browser_session_id")
        if session_id:
            return session_id
        account_id = ctx.get("account_id")
        return self._sessions_by_account.get(account_id) if account_id else None

    # -- operations ----------------------------------------------------------
    def discovery(self, **ctx) -> dict:
        account_type = self._account_type_from_ctx(ctx)
        desc = self.descriptor_for(account_type, self._base)
        return {
            "provider_id": PROVIDER_ID,
            "display_name": desc.display_name,
            "status": "descriptor",
            "official_domain": desc.official_domain,
            "official_domains": self._official_domains(),
            "registration_url": desc.registration_url,
            "registration_urls": dict(REGISTRATION_URLS),
            "account_types": list(desc.account_types),
            "account_type": account_type,
            "has_api": desc.has_api,
            "has_oauth": desc.has_oauth,
            "browser_required": desc.browser_required,
            "requires_email": desc.requires_email,
            "requires_phone": desc.requires_phone,
            "requires_captcha": desc.requires_captcha,
            "requires_mfa": desc.requires_mfa,
            "requires_identity_verification": desc.requires_identity_verification,
            "requires_human_identity_verification": account_type in _IDENTITY_REQUIRED_TYPES,
            "requires_payment": desc.requires_payment,
            "automation_policy": desc.automation_policy.value,
            "policy_source": desc.policy_source,
        }

    def registration(self, **ctx) -> dict:
        account_type = self._account_type_from_ctx(ctx)
        desc = self.descriptor_for(account_type, self._base)
        identity = self._resolve_identity(ctx.get("identity_id"))
        session_id = ctx.get("browser_session_id")
        account_id = ctx.get("account_id")
        if not session_id or identity is None:
            return {"status": "unavailable", "reason": "missing_session_or_identity",
                    "account_type": account_type, "requires_human": False}
        if account_id:
            self._sessions_by_account[account_id] = session_id
        self._sessions_by_account[session_id] = session_id

        # 1. navigate to the official registration URL (host validated below)
        self._perform(session_id, "navigate",
                      {"url": desc.registration_url, "enforce_domain": True})
        current = self._inspect(session_id)
        current_url = (current or {}).get("url") or desc.registration_url
        self._assert_official_host(current_url)

        # 2. fill the non-secret identity fields
        self._perform(session_id, "fill",
                      {"selector": "#ap_customer_name", "value": identity.display_name})
        if getattr(identity, "email", None):
            self._perform(session_id, "fill",
                          {"selector": "#ap_email", "value": identity.email})

        # 3. generate a strong password and store it in the Vault (ref only)
        password = _generate_password()
        path = (vault_reference_for(PROVIDER_ID, account_id) if account_id
                else f"secrets/accounts/{PROVIDER_ID}/{account_type}")
        password_ref = self._vault_store(path, password)
        # credential entry only after the official-host check above
        browser_security.assert_credential_target(
            current_url, (self._base or desc).official_domain)
        self._perform(session_id, "fill",
                      {"selector": "#ap_password", "value": password, "credential": True})
        self._perform(session_id, "fill",
                      {"selector": "#ap_password_check", "value": password, "credential": True})
        password = None  # drop the plaintext from this frame

        # 4. submit and detect the resulting page state
        self._perform(session_id, "click", {"selector": "#continue"})
        snapshot = self._inspect(session_id)
        captcha = detect_captcha(snapshot)
        created = _detect_account_created(snapshot)
        requires_human_id = account_type in _IDENTITY_REQUIRED_TYPES
        blocking = bool(captcha["detected"]) and not created
        action_type = ("ID_VERIFICATION" if requires_human_id
                       else ("CAPTCHA" if blocking else None))
        return {
            "status": "created" if created else ("blocked" if (blocking or requires_human_id)
                                                 else "submitted"),
            "account_type": account_type,
            "password_ref": password_ref,
            "vault_stored": bool(password_ref),
            "captcha_detected": bool(captcha["detected"]),
            "account_created_detected": bool(created),
            "requires_human": bool(blocking or requires_human_id),
            "action_type": action_type,
            "requires_identity_verification": requires_human_id,
        }

    def verification(self, **ctx) -> dict:
        """Enter a relayed email/phone OTP code. The code is never echoed."""
        code = ctx.get("code")
        if not code:
            return {"status": "no_code_received", "handled_by": "mail_worker"}
        session_id = self._session_for(ctx)
        if not session_id:
            return {"status": "no_session"}
        self._perform(session_id, "fill",
                      {"selector": "#cvf-input-code", "value": code, "credential": True})
        self._perform(session_id, "click", {"selector": "#cvf-submit-otp-button"})
        return {"status": "otp_submitted"}

    def authentication(self, **ctx) -> dict:
        session_id = self._session_for(ctx)
        if not session_id:
            return {"status": "no_session"}
        self._perform(session_id, "navigate", {"url": SIGNIN_URL, "enforce_domain": True})
        snapshot = self._inspect(session_id)
        return {"status": "sign_in_required",
                "auth_state": detect_auth_state(snapshot),
                "mfa_may_be_required": True}

    def security_setup(self, **ctx) -> dict:
        session_id = self._session_for(ctx)
        if session_id:
            self._perform(session_id, "navigate",
                          {"url": SECURITY_URL, "enforce_domain": True})
        account_id = ctx.get("account_id")
        reference = vault_reference_for(PROVIDER_ID, account_id) if account_id else None
        return {"status": "security_settings_reviewed",
                "mfa_offered": True, "mfa_enabled": False, "vault_reference": reference}

    def profile_setup(self, **ctx) -> dict:
        identity = self._resolve_identity(ctx.get("identity_id"))
        session_id = self._session_for(ctx)
        name = getattr(identity, "display_name", None) if identity else None
        if session_id and name:
            self._perform(session_id, "fill",
                          {"selector": "#enterAddressFullName", "value": name})
        return {"status": "profile_synced", "name_set": bool(name),
                "address_provided": False}  # never fabricate an address

    def recovery(self, **ctx) -> dict:
        identity = self._resolve_identity(ctx.get("identity_id"))
        return {
            "status": "recovery_reviewed",
            "email_recovery": bool(identity and getattr(identity, "email", None)),
            "phone_recovery": bool(identity and getattr(identity, "phone", None)),
            "values_persisted": False,
        }

    def publishing(self, **ctx):
        account_type = self._account_type_from_ctx(ctx)
        if account_type == "customer":
            return NOT_APPLICABLE
        return {"status": "not_implemented", "account_type": account_type,
                "requires_human": True,
                "reason": "seller/associates publishing is out of scope for STEP 7"}

    def account_status(self, **ctx) -> dict:
        session_id = self._session_for(ctx)
        account_type = self._account_type_from_ctx(ctx)
        if not session_id:
            return {"status": "unknown", "reason": "no_browser_session",
                    "account_type": account_type}
        snapshot = self._inspect(session_id)
        created = _detect_account_created(snapshot)
        captcha = detect_captcha(snapshot)
        return {
            "status": "active" if created else "pending",
            "account_type": account_type,
            "auth_state": detect_auth_state(snapshot),
            "captcha_detected": bool(captcha["detected"]),
            "source": "browser_snapshot",
        }


def register_amazon(adapter: Optional[AmazonAdapter] = None) -> AmazonAdapter:
    """Register the shipped Amazon adapter (idempotent)."""
    adapter = adapter or AmazonAdapter()
    try:
        return store.register_adapter(adapter)
    except store.DuplicateAdapter:
        return store.get_adapter(PROVIDER_ID)


__all__ = [
    "PROVIDER_ID",
    "ACCOUNT_TYPES",
    "REGISTRATION_URLS",
    "SIGNIN_URL",
    "SECURITY_URL",
    "AmazonAdapter",
    "register_amazon",
    "resolve_account_type",
]
