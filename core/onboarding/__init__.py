"""core.onboarding — KAI Universal Account Registration, STEP 6.

The **Universal Onboarding State Machine**: the engine that orchestrates every
prior step (identity, accounts, provider adapter + policy gate, browser
operator, email worker, SMS/OTP worker, human-action paging) into one
persistent, resumable, idempotent flow.

Reuses the platform's real primitives: ``core.memory`` (persistence),
``core.kai_event_bus`` (events), ``core.audit_logger`` (audit),
``core.kai_missions`` (mission-backed state), ``core.providers`` (gate +
adapters), ``core.identity`` / ``core.accounts`` (registries), ``core.browser``,
``core.mail``, ``core.sms`` and ``core.notify``.

    from core.onboarding import start_onboarding, resume_onboarding

No secret is ever stored: only vault *paths* and evidence hashes.
"""

from core.onboarding.schema import (
    ErrorEntry,
    EvidenceEntry,
    OnboardingSession,
    OnboardingState,
    OnboardingStatus,
    PAUSED_STATE,
    STATE_SEQUENCE,
    TERMINAL_STATES,
    now_iso,
)
from core.onboarding.engine import (
    drive,
    hash_payload,
    hash_text,
    is_applicable,
    next_applicable,
    step,
)
from core.onboarding.manager import (
    Pipeline,
    SessionNotFound,
    cancel_onboarding,
    default_pipeline,
    get_session,
    health,
    list_sessions,
    resume_onboarding,
    run_onboarding,
    start_onboarding,
)

__all__ = [
    # schema
    "OnboardingState",
    "OnboardingStatus",
    "OnboardingSession",
    "EvidenceEntry",
    "ErrorEntry",
    "STATE_SEQUENCE",
    "TERMINAL_STATES",
    "PAUSED_STATE",
    "now_iso",
    # engine
    "step",
    "drive",
    "is_applicable",
    "next_applicable",
    "hash_text",
    "hash_payload",
    # manager
    "SessionNotFound",
    "Pipeline",
    "default_pipeline",
    "start_onboarding",
    "resume_onboarding",
    "run_onboarding",
    "cancel_onboarding",
    "get_session",
    "list_sessions",
    "health",
]
