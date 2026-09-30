"""Built-in provider catalog (pure data, no I/O).

Descriptors shipped with KAI are the *authoritative* reference data and live in
code, not in ``memory/``: ``automation_policy`` is safety-critical, and a
runtime-editable file must not be able to silently flip PROHIBITED → ALLOWED.
Runtime *policy overrides* (human-confirmed decisions) are stored separately
via :mod:`core.providers.store` and audited.

STEP 2 shipped the stub/example providers. STEP 7 adds the first **real**
provider — Amazon — with its automation policy deliberately recorded as
UNKNOWN so the ToS/legality gate forces human confirmation before any
automated step (see ``AMAZON_POLICY_SOURCE``).
"""

from __future__ import annotations

from core.providers.schema import AutomationPolicy, ProviderDescriptor

#: Amazon's automation-policy provenance. We do NOT assume permission: the
#: Conditions of Use restrict automated access and no clause granting KAI
#: permission was found, so the policy stays UNKNOWN (human confirmation
#: required by the gate).
AMAZON_POLICY_SOURCE = (
    "Amazon Conditions of Use — "
    "https://www.amazon.com/gp/help/customer/display.html?nodeId=508088 "
    "(also https://www.amazon.com/conditionsofuse). The Conditions restrict "
    "access to the Amazon Services by 'robot, spider, scraper or other "
    "automated means' without Amazon's express written permission. KAI does NOT "
    "assume permission: automation_policy is recorded as UNKNOWN and the ToS "
    "gate requires human confirmation before any automated step."
)

BUILTIN_DESCRIPTORS: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(
        provider_id="generic",
        display_name="Generic Provider (stub)",
        official_domain="example.invalid",
        registration_url=None,
        account_types=["generic"],
        has_api=False,
        has_oauth=False,
        browser_required=True,
        requires_email=True,
        requires_captcha=True,
        automation_policy=AutomationPolicy.UNKNOWN,
        policy_source="shipped stub: automation policy not yet evaluated",
        regions=["*"],
        notes="Placeholder adapter used to exercise the interface + policy gate.",
    ),
    ProviderDescriptor(
        provider_id="proton",
        display_name="Proton Mail",
        official_domain="proton.me",
        registration_url="https://proton.me/mail",
        account_types=["email"],
        has_api=False,
        has_oauth=False,
        browser_required=True,
        requires_email=False,
        requires_phone=False,
        requires_captcha=True,
        requires_mfa=True,
        automation_policy=AutomationPolicy.UNKNOWN,
        policy_source="STEP 4: KAI mailbox read via Proton Bridge (localhost IMAP/SMTP)",
        regions=["*"],
        notes=(
            "KAI-owned mailbox. Inbound/outbound mail is handled by the STEP 4 "
            "email worker via Proton Bridge; this is not provider-registration "
            "automation, so the ToS gate does not gate mail reads."
        ),
    ),
    ProviderDescriptor(
        provider_id="amazon",
        display_name="Amazon",
        official_domain="amazon.com",
        registration_url="https://www.amazon.com/ap/register",
        account_types=["customer", "business", "seller", "associates"],
        has_api=False,
        has_oauth=False,
        browser_required=True,
        requires_email=True,
        requires_phone=True,
        requires_captcha=True,
        requires_mfa=False,  # optional for Amazon
        # customer default; seller/business require KYC (always a human step)
        requires_identity_verification=False,
        requires_payment=True,  # card on file
        automation_policy=AutomationPolicy.UNKNOWN,
        policy_source=AMAZON_POLICY_SOURCE,
        regions=[
            "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr", "amazon.es",
            "amazon.it", "amazon.com.au", "amazon.in", "amazon.co.jp",
        ],
        notes=(
            "STEP 7 first real provider adapter. Customer onboarding is browser + "
            "email OTP + SMS OTP + CAPTCHA human takeover; seller/business add "
            "human identity verification. Automation NOT assumed permitted "
            "(policy UNKNOWN)."
        ),
    ),
)
