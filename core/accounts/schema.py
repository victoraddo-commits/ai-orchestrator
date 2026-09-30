"""Account Registry schemas (Pydantic v2).

An account is one registration KAI holds with a provider. It references a
digital identity, an optional browser profile, a mission and a vault
*reference* — never a password. ``extra="forbid"`` enforces that boundary.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from core.agentguard.guard import RiskLevel


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def vault_reference_for(provider: str, account_id: str) -> str:
    """Canonical vault *path* for an account credential (path only, no value)."""
    slug = provider.strip().lower().replace(" ", "_").replace(".", "_")
    return f"secrets/accounts/{slug}/{account_id}"


class VerificationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    NOT_TESTED = "NOT TESTED"
    NOT_APPLICABLE = "NOT APPLICABLE"


class SecurityStatus(str, enum.Enum):
    SECURE = "SECURE"
    AT_RISK = "AT RISK"
    UNKNOWN = "UNKNOWN"
    COMPROMISED = "COMPROMISED"
    NOT_APPLICABLE = "NOT APPLICABLE"


class AccountStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class AccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    provider: str
    provider_account_id: Optional[str] = None
    account_type: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    digital_identity: Optional[str] = None
    browser_profile: Optional[str] = None
    creation_time: str
    verification_status: VerificationStatus = VerificationStatus.NOT_TESTED
    security_status: SecurityStatus = SecurityStatus.UNKNOWN
    vault_reference: Optional[str] = None
    mission_id: Optional[str] = None
    last_verified: Optional[str] = None
    last_activity: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    status: AccountStatus = AccountStatus.active
    created_at: str
    updated_at: str


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=100)
    provider_account_id: Optional[str] = None
    account_type: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    digital_identity: Optional[str] = None
    browser_profile: Optional[str] = None
    vault_reference: Optional[str] = None
    mission_id: Optional[str] = None
    verification_status: VerificationStatus = VerificationStatus.NOT_TESTED
    security_status: SecurityStatus = SecurityStatus.UNKNOWN
    risk_level: RiskLevel = RiskLevel.MEDIUM


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_account_id: Optional[str] = None
    account_type: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    digital_identity: Optional[str] = None
    browser_profile: Optional[str] = None
    vault_reference: Optional[str] = None
    mission_id: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None
    security_status: Optional[SecurityStatus] = None
    risk_level: Optional[RiskLevel] = None
    last_verified: Optional[str] = None
    last_activity: Optional[str] = None
