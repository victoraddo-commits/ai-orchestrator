"""Provider Registry schemas (Pydantic v2).

A *provider descriptor* is the reference data KAI needs to reason about a
third-party service: who it is, where you register, what it requires, and —
critically — whether automating it is permitted. No credentials ever live here;
``extra="forbid"`` enforces that boundary (mirrors ``core.identity`` /
``core.accounts``).

The gate decision is also modelled here so every caller shares one structured
shape: ``policy_gate()`` returns a :class:`GateDecision`.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationPolicy(str, enum.Enum):
    """Whether KAI may automate interactions with a provider.

    DEFAULT is :attr:`UNKNOWN` — an unevaluated provider must never be
    silently automated (see :func:`core.providers.gate.policy_gate`).
    """

    ALLOWED = "ALLOWED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class GateOutcome(str, enum.Enum):
    """Structured result of a policy-gate evaluation."""

    ALLOW = "allow"
    REQUIRE_HUMAN_CONFIRMATION = "require_human_confirmation"
    REFUSE_AUTOMATION = "refuse_automation"


class ProviderDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    official_domain: Optional[str] = None
    registration_url: Optional[str] = None
    account_types: list[str] = Field(min_length=1)
    has_api: bool = False
    has_oauth: bool = False
    browser_required: bool = False
    requires_email: bool = False
    requires_phone: bool = False
    requires_captcha: bool = False
    requires_mfa: bool = False
    requires_identity_verification: bool = False
    requires_payment: bool = False
    automation_policy: AutomationPolicy = AutomationPolicy.UNKNOWN
    policy_source: Optional[str] = None
    regions: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @field_validator("provider_id")
    @classmethod
    def _check_provider_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _PROVIDER_ID_RE.match(normalized):
            raise ValueError(f"invalid provider_id: {value!r}")
        return normalized

    @field_validator("official_domain")
    @classmethod
    def _check_domain(cls, value: Optional[str]) -> Optional[str]:
        if value:
            normalized = value.strip().lower()
            if not _DOMAIN_RE.match(normalized):
                raise ValueError(f"invalid official_domain: {value!r}")
            return normalized
        return value


class GateDecision(BaseModel):
    """Structured, auditable result of a ToS/legality policy gate evaluation.

    ``requires_human`` is True whenever the automated path must not proceed
    without a human: UNKNOWN (confirm) and PROHIBITED (take over).
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    decision: GateOutcome
    reason: str
    policy_source: Optional[str] = None
    requires_human: bool
    automation_policy: AutomationPolicy
    evaluated_at: str
