"""core.browser — KAI Browser/Web Operator (STEP 3, Universal Account Registration).

The execution engine the onboarding agent drives. Profiles are isolated per
(identity, provider); operations are validated and policy/URL-checked before
credentials; sessions persist (storage_state) for crash recovery; a human
takeover loop pauses a mission and resumes it only when the page state changes.

Split (see docs): the Playwright engine + runtime state run on CT110
``kai-browser``; orchestration/governance (missions, gate, event bus, audit) run
on CT111 and drive CT110 through :mod:`core.browser.client`.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.browser.schema import (
    AuthState,
    BrowserProfile,
    ElementRef,
    OpName,
    PageSnapshot,
    SessionState,
    SessionStatus,
    TakeoverRecord,
    TakeoverStatus,
    browser_vault_namespace,
    now_iso,
    profile_id_for,
    slug,
)
from core.browser.security import (
    CredentialTargetRefused,
    DomainMismatch,
    EvaluateRefused,
    RedirectBlocked,
    UnsafeUrl,
    assert_credential_target,
    assert_navigation_stays_on_site,
    guard_evaluate,
    is_same_site,
    redact_text,
    sanitize_untrusted_text,
    validate_navigation_url,
)
from core.browser.operations import InvalidOperation, OP_SPECS, validate_operation
from core.browser import detectors
from core.browser.profiles import ProfileNotFound, ProfileStore
from core.browser.sessions import SessionNotFound, SessionStore
from core.browser.takeover import TakeoverNotFound, TakeoverStore
from core.browser.sinks import EventSink, JsonlSink, KaiBusSink, RecordingSink
from core.browser.manager import (
    ApprovalRequired,
    BrowserOperator,
    BrowserOperatorError,
    TooManySessions,
)


def default_data_dir() -> Path:
    return Path(os.environ.get("KAI_BROWSER_DATA_DIR", "/opt/kai-browser/data"))


def get_operator(*, engine=None, sink: EventSink | None = None, **kwargs) -> BrowserOperator:
    """Build a BrowserOperator from the environment (engine optional)."""
    root = kwargs.pop("root", None) or default_data_dir()
    headless = os.environ.get("KAI_BROWSER_HEADLESS", "1") == "1"
    max_sessions = int(os.environ.get("KAI_BROWSER_MAX_SESSIONS", "2"))
    novnc_url = os.environ.get("KAI_BROWSER_NOVNC_URL") or None
    return BrowserOperator(
        Path(root),
        engine=engine,
        sink=sink,
        novnc_url=novnc_url,
        max_concurrent=max_sessions,
        headless=headless,
        **kwargs,
    )


__all__ = [
    # schema
    "AuthState", "BrowserProfile", "ElementRef", "OpName", "PageSnapshot",
    "SessionState", "SessionStatus", "TakeoverRecord", "TakeoverStatus",
    "browser_vault_namespace", "now_iso", "profile_id_for", "slug",
    # security
    "UnsafeUrl", "DomainMismatch", "CredentialTargetRefused", "RedirectBlocked",
    "EvaluateRefused", "assert_credential_target", "assert_navigation_stays_on_site",
    "guard_evaluate", "is_same_site", "redact_text", "sanitize_untrusted_text",
    "validate_navigation_url",
    # operations / detectors
    "InvalidOperation", "OP_SPECS", "validate_operation", "detectors",
    # stores
    "ProfileStore", "ProfileNotFound", "SessionStore", "SessionNotFound",
    "TakeoverStore", "TakeoverNotFound",
    # sinks
    "EventSink", "JsonlSink", "KaiBusSink", "RecordingSink",
    # manager
    "BrowserOperator", "BrowserOperatorError", "TooManySessions", "ApprovalRequired",
    # factory
    "default_data_dir", "get_operator",
]
