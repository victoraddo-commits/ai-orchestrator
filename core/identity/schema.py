"""Digital Identity Manager schemas (Pydantic v2).

No secret is ever a field here — credentials live in the vault and are
referenced only through ``vault_namespace``. ``extra="forbid"`` enforces that
at the model boundary.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity_vault_namespace(identity_id: str) -> str:
    """Canonical vault *path* for an identity's credentials (no value)."""
    return f"secrets/identities/{identity_id}"


class IdentityStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    archived = "archived"


class SecurityProfile(str, enum.Enum):
    standard = "standard"
    hardened = "hardened"
    restricted = "restricted"


class RecoveryConfig(BaseModel):
    """Non-secret recovery configuration. Never holds a code or answer value."""

    model_config = ConfigDict(extra="forbid")

    method: Optional[str] = None
    contact_ref: Optional[str] = None
    enabled: bool = False
    notes: Optional[str] = None


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError(f"invalid email: {value!r}")
    return value


class DigitalIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: str
    display_name: str
    email: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    phone: Optional[str] = None
    phones: list[str] = Field(default_factory=list)
    domain: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    browser_profiles: list[str] = Field(default_factory=list)
    vault_namespace: str
    security_profile: SecurityProfile = SecurityProfile.standard
    provider_permissions: dict[str, list[str]] = Field(default_factory=dict)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    is_default: bool = False
    status: IdentityStatus = IdentityStatus.active
    created_at: str
    updated_at: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: Optional[str]) -> Optional[str]:
        return _validate_email(value) if value else value

    @field_validator("emails")
    @classmethod
    def _check_emails(cls, value: list[str]) -> list[str]:
        return [_validate_email(v) for v in value]


class IdentityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = None
    phone: Optional[str] = None
    domain: Optional[str] = None
    browser_profiles: list[str] = Field(default_factory=list)
    vault_namespace: Optional[str] = None
    security_profile: SecurityProfile = SecurityProfile.standard
    provider_permissions: dict[str, list[str]] = Field(default_factory=dict)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    is_default: bool = False

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: Optional[str]) -> Optional[str]:
        return _validate_email(value) if value else value

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: Optional[str]) -> Optional[str]:
        if value and not _DOMAIN_RE.match(value):
            raise ValueError(f"invalid domain: {value!r}")
        return value


class IdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    security_profile: Optional[SecurityProfile] = None
    provider_permissions: Optional[dict[str, list[str]]] = None
    recovery: Optional[RecoveryConfig] = None
    vault_namespace: Optional[str] = None
    status: Optional[IdentityStatus] = None
