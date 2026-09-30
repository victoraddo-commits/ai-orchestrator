"""Browser-Operator schemas (Pydantic v2).

STEP 3 of Universal Account Registration: the browser/web operator is the
execution engine the onboarding agent drives. No credential *value* is ever a
field here — sessions reference a vault namespace; profile metadata holds only
paths. ``extra="forbid"`` enforces that boundary at the model edge, mirroring
``core.identity`` / ``core.accounts`` / ``core.providers``.
"""

from __future__ import annotations

import enum
import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    """Filesystem-safe, collision-resistant slug for an identity/provider token."""
    raw = (value or "").strip().lower()
    safe = _SLUG_RE.sub("-", raw).strip("-") or "x"
    safe = safe[:48]
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{safe}-{digest}"


def profile_id_for(identity_id: str, provider_id: str) -> str:
    return f"prof-{slug(identity_id)}-{slug(provider_id)}"


def browser_vault_namespace(identity_id: str) -> str:
    """Canonical vault path for browser-profile secrets (proxy creds, etc.)."""
    return f"secrets/browser/{identity_id}"


class SessionStatus(str, enum.Enum):
    created = "created"
    active = "active"
    paused = "paused"
    ended = "ended"
    error = "error"


class AuthState(str, enum.Enum):
    logged_in = "logged_in"
    logged_out = "logged_out"
    unknown = "unknown"


class TakeoverStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    expired = "expired"
    cancelled = "cancelled"


class OpName(str, enum.Enum):
    navigate = "navigate"
    inspect = "inspect"
    fill = "fill"
    click = "click"
    screenshot = "screenshot"
    upload_file = "upload_file"
    evaluate = "evaluate"
    wait_for = "wait_for"


class BrowserProfile(BaseModel):
    """Metadata for one isolated browser profile (never credentials)."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    identity_id: str
    provider_id: str
    dir: str
    user_data_dir: str
    storage_state_path: str
    vault_namespace: str
    created_at: str
    updated_at: str


class ElementRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = ""
    name: str = ""
    type: str = ""
    selector: str = ""


class PageSnapshot(BaseModel):
    """A sanitized, untrusted view of a page.

    ``text`` is accessible/normalized text, never raw HTML handed to the LLM
    unvalidated. ``untrusted`` is always True: page content must never become
    agent instructions.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = ""
    title: str = ""
    text: str = ""
    elements: list[ElementRef] = Field(default_factory=list)
    has_password_field: bool = False
    console: list[str] = Field(default_factory=list)
    untrusted: bool = True
    truncated: bool = False


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    mission_id: Optional[str] = None
    profile_id: str
    identity_id: str
    provider_id: str
    status: SessionStatus = SessionStatus.created
    auth_state: AuthState = AuthState.unknown
    current_url: Optional[str] = None
    storage_state_path: Optional[str] = None
    takeover_id: Optional[str] = None
    last_action: Optional[str] = None
    headless: bool = True
    created_at: str
    updated_at: str


class TakeoverRecord(BaseModel):
    """A human-takeover request; survives restart so a mission can resume."""

    model_config = ConfigDict(extra="forbid")

    takeover_id: str
    session_id: str
    mission_id: Optional[str] = None
    provider_id: Optional[str] = None
    identity_id: Optional[str] = None
    action_required: str
    reason: str
    current_state: dict = Field(default_factory=dict)
    instructions: str = ""
    no_vnc_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    resume_condition: dict = Field(default_factory=dict)
    status: TakeoverStatus = TakeoverStatus.pending
    expires_at: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None

    @field_validator("current_state")
    @classmethod
    def _state_is_plain(cls, value: dict) -> dict:
        # Takeover state is metadata only; reject embedded secret-looking keys.
        from core.browser.security import assert_no_secret_keys

        assert_no_secret_keys(value)
        return value
