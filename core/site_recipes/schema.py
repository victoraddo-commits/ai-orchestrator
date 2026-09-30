"""Site Recipe / Site Profile schemas (Pydantic v2).

A recipe is *metadata only*: domains, URLs, selectors, symbolic value sources
and booleans. It MUST NOT contain a credential value. ``extra="forbid"``
enforces that at the model edge, mirroring ``core.providers.schema``.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.providers.schema import AutomationPolicy

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_domain(value: str) -> str:
    """Lower-case host from a URL/domain/email; strip scheme, path, port, www."""
    raw = (value or "").strip().lower()
    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("@")[-1]
    raw = raw.split("/")[0]
    raw = raw.split(":")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    return raw


class FlowType(str, enum.Enum):
    single_page = "single_page"
    multi_step = "multi_step"
    sso_only = "sso_only"
    invite_only = "invite_only"
    unknown = "unknown"


class RecipeStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    stale = "stale"


class RecipeSource(str, enum.Enum):
    seeded = "seeded"
    learned = "learned"


class SiteRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: bool = False
    phone: bool = False
    captcha: bool = False
    mfa: bool = False
    payment: bool = False
    kyc: bool = False
    email_verify: bool = False
    phone_verify: bool = False


class FieldSpec(BaseModel):
    """One form field. ``value_source`` is symbolic (never a secret value)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    selector: str = Field(min_length=1, max_length=300)
    kind: str = "text"
    required: bool = True
    value_source: Optional[str] = None
    step: int = 0


class RecipeStep(BaseModel):
    """One browser-operator step. ``value_source`` is symbolic, never a value."""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    action: str
    selector: Optional[str] = None
    url: Optional[str] = None
    value_source: Optional[str] = None
    description: Optional[str] = None


class SiteRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    signup_url: Optional[str] = None
    flow_type: FlowType = FlowType.unknown
    fields: list[FieldSpec] = Field(default_factory=list)
    requirements: SiteRequirements = Field(default_factory=SiteRequirements)
    steps: list[RecipeStep] = Field(default_factory=list)
    verification_flow: Optional[str] = None
    confidence: float = 0.0
    source: RecipeSource = RecipeSource.learned
    status: RecipeStatus = RecipeStatus.draft
    version: int = 1
    last_verified_at: Optional[str] = None
    evidence_ref: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        normalized = normalize_domain(value)
        if not _DOMAIN_RE.match(normalized):
            raise ValueError(f"invalid domain: {value!r}")
        return normalized


class SiteProfile(BaseModel):
    """What the classifier knows about a domain before any recipe exists."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    signup_url: Optional[str] = None
    registrable: bool = True
    flow_type: FlowType = FlowType.unknown
    requirements: SiteRequirements = Field(default_factory=SiteRequirements)
    automation_policy: AutomationPolicy = AutomationPolicy.UNKNOWN
    policy_source: Optional[str] = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        normalized = normalize_domain(value)
        if not _DOMAIN_RE.match(normalized):
            raise ValueError(f"invalid domain: {value!r}")
        return normalized
