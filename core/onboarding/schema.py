"""Universal Onboarding State Machine schemas (Pydantic v2).

STEP 6 of Universal Account Registration: the engine that orchestrates the
identity, account, provider, browser, mail, SMS and human-action subsystems
into one persistent, resumable, idempotent flow.

Boundaries enforced at the model edge (mirrors ``core.identity`` /
``core.accounts`` / ``core.providers``):

* no credential is ever a field — the vault is referenced by *path* only;
* ``extra="forbid"`` rejects any unknown/credential field;
* evidence entries carry references and hashes, never values.

The state list is the directive's, verbatim. ``COMPLETED`` ends the happy path;
``PAUSED_HUMAN`` / ``FAILED`` / ``CANCELLED`` are the control states.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OnboardingState(str, enum.Enum):
    DISCOVER_PROVIDER = "DISCOVER_PROVIDER"
    CHECK_EXISTING_ACCOUNT = "CHECK_EXISTING_ACCOUNT"
    SELECT_IDENTITY = "SELECT_IDENTITY"
    SELECT_PROVIDER_ADAPTER = "SELECT_PROVIDER_ADAPTER"
    CHECK_REQUIREMENTS = "CHECK_REQUIREMENTS"
    PREPARE_BROWSER = "PREPARE_BROWSER"
    START_REGISTRATION = "START_REGISTRATION"
    ENTER_INFORMATION = "ENTER_INFORMATION"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    SMS_VERIFICATION = "SMS_VERIFICATION"
    MFA = "MFA"
    #: CAPTCHA / payment — the generic human-takeover state.
    CAPTCHA_HUMAN_TAKEOVER = "CAPTCHA/HUMAN_TAKEOVER"
    IDENTITY_VERIFICATION = "IDENTITY_VERIFICATION"
    SECURITY_CONFIGURATION = "SECURITY_CONFIGURATION"
    ACCOUNT_VERIFICATION = "ACCOUNT_VERIFICATION"
    ACCOUNT_REGISTRY = "ACCOUNT_REGISTRY"
    VAULT = "VAULT"
    END_TO_END_TEST = "END_TO_END_TEST"
    COMPLETED = "COMPLETED"
    PAUSED_HUMAN = "PAUSED_HUMAN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: The canonical linear happy-path, in the directive's order.
STATE_SEQUENCE: tuple[OnboardingState, ...] = (
    OnboardingState.DISCOVER_PROVIDER,
    OnboardingState.CHECK_EXISTING_ACCOUNT,
    OnboardingState.SELECT_IDENTITY,
    OnboardingState.SELECT_PROVIDER_ADAPTER,
    OnboardingState.CHECK_REQUIREMENTS,
    OnboardingState.PREPARE_BROWSER,
    OnboardingState.START_REGISTRATION,
    OnboardingState.ENTER_INFORMATION,
    OnboardingState.EMAIL_VERIFICATION,
    OnboardingState.SMS_VERIFICATION,
    OnboardingState.MFA,
    OnboardingState.CAPTCHA_HUMAN_TAKEOVER,
    OnboardingState.IDENTITY_VERIFICATION,
    OnboardingState.SECURITY_CONFIGURATION,
    OnboardingState.ACCOUNT_VERIFICATION,
    OnboardingState.ACCOUNT_REGISTRY,
    OnboardingState.VAULT,
    OnboardingState.END_TO_END_TEST,
    OnboardingState.COMPLETED,
)

#: States that end the run (no further transitions).
TERMINAL_STATES: frozenset[OnboardingState] = frozenset(
    {OnboardingState.COMPLETED, OnboardingState.FAILED, OnboardingState.CANCELLED}
)

#: The single holding state while a human is required.
PAUSED_STATE = OnboardingState.PAUSED_HUMAN


class OnboardingStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED_HUMAN = "paused_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceEntry(BaseModel):
    """One item in the per-transition evidence ledger.

    ``ref`` names an artifact (screenshot path, email id, account id, takeover
    id); ``hash`` is a content hash of a raw provider response / page state.
    Neither carries a secret value.
    """

    model_config = ConfigDict(extra="forbid")

    state: str
    kind: str
    at: str
    ref: Optional[str] = None
    hash: Optional[str] = None
    detail: dict = Field(default_factory=dict)


class ErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    error: str
    at: str
    retryable: bool = True


class OnboardingSession(BaseModel):
    """Persistent state-machine snapshot for one provider onboarding.

    Every field is safe to persist: identifiers and vault *paths* only.
    """

    model_config = ConfigDict(extra="forbid")

    mission_id: str
    objective: str
    provider_id: str
    identity_id: Optional[str] = None
    current_state: OnboardingState
    status: OnboardingStatus = OnboardingStatus.ACTIVE
    completed_states: list[str] = Field(default_factory=list)
    skipped_states: list[str] = Field(default_factory=list)
    pending_state: Optional[str] = None
    pre_pause_state: Optional[str] = None
    browser_profile: Optional[str] = None
    browser_session_id: Optional[str] = None
    account_id: Optional[str] = None
    vault_reference: Optional[str] = None
    verification_state: dict = Field(default_factory=dict)
    human_action_required: bool = False
    human_action_id: Optional[str] = None
    human_action_type: Optional[str] = None
    requirements: dict = Field(default_factory=dict)
    retry_count: int = 0
    errors: list[ErrorEntry] = Field(default_factory=list)
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


__all__ = [
    "OnboardingState",
    "OnboardingStatus",
    "STATE_SEQUENCE",
    "TERMINAL_STATES",
    "PAUSED_STATE",
    "EvidenceEntry",
    "ErrorEntry",
    "OnboardingSession",
    "now_iso",
]
