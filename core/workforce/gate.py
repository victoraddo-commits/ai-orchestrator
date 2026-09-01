"""Delegate gate — the single enforcement point every provider dispatch must
pass. Denials are advisory-safe: an unavailable registry NEVER blocks routing
(backward compatible), but a registered worker that is dead/paused/capability-
mismatched/environment-ineligible is refused before any network call.
"""
from __future__ import annotations

import datetime
from typing import Optional

from core.ai.ai_router import AllProvidersFailed
from core.workforce import registry
from core.logger import info as _log

# Providers that may never serve production work (Ox Alpha rule). Mirrors
# bootstrap._DEV_ONLY_PROVIDERS — keep both in sync via this import.
from core.workforce.bootstrap import _DEV_ONLY_PROVIDERS

_AUDIT_LOG = "workforce_gate_audit.json"

DESTRUCTIVE_OPERATIONS = frozenset({
    "delete_files",
    "terminate_worker",
    "kill_provider",
    "force_deploy",
    "modify_secrets",
    "network_bridge",
    "data_export",
    "admin_action",
})


class NoCapableWorkerError(AllProvidersFailed):
    """Raised when candidates existed but every one was denied by policy."""


def check(provider_name: str, capability: str, production: bool = True):
    """Return a denial reason string, or None when admissible.

    Unknown/unregistered providers are admissible (backward compat)."""
    try:
        record = registry.get(f"provider:{provider_name}")
    except Exception:
        return None
    if record is None:
        return None

    # Hard guard independent of the stored record (Ox Alpha rule).
    if production and provider_name.lower().replace("-", "_") in _DEV_ONLY_PROVIDERS:
        reason = "development-only worker cannot serve production"
        _deny(provider_name, capability, reason)
        return reason

    if record.status in ("dead", "paused"):
        reason = f"worker is {record.status}"
        _deny(provider_name, capability, reason)
        return reason

    if capability not in record.capabilities and "generate" not in record.capabilities:
        reason = f"worker lacks capability {capability!r}"
        _deny(provider_name, capability, reason)
        return reason

    return None


def check_operation(provider_name: str, operation: str) -> Optional[str]:
    """Return a denial reason string, or None when the operation is authorized.

    Unknown operations pass through (return None) for forward compatibility.
    Unknown/unregistered providers are admissible (backward compat)."""
    if operation not in DESTRUCTIVE_OPERATIONS:
        return None
    try:
        record = registry.get(f"provider:{provider_name}")
    except Exception:
        return None
    if record is None:
        return None
    authority = getattr(record, "destructive_authority", {}) or {}
    if not authority.get(operation, False):
        reason = f"worker lacks destructive_authority.{operation}"
        _deny_operation(provider_name, operation, reason)
        return reason
    return None


def _deny_operation(provider_name: str, operation: str, reason: str) -> None:
    _log(f"workforce gate: DENIED operation {operation} for {provider_name}: {reason}")
    try:
        from core.memory import load as _load, save as _save
        data = _load(_AUDIT_LOG) or {"schema_version": 1, "records": []}
        records = data.get("records", data if isinstance(data, list) else [])
        records.append({
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider": provider_name, "operation": operation, "reason": reason,
        })
        _save(_AUDIT_LOG, {"schema_version": 1, "records": records[-500:]})
    except Exception:
        pass


def _deny(provider_name: str, capability: str, reason: str) -> None:
    _log(f"workforce gate: DENIED {provider_name} for {capability}: {reason}")
    try:
        from core.memory import load as _load, save as _save
        data = _load(_AUDIT_LOG) or {"schema_version": 1, "records": []}
        records = data.get("records", data if isinstance(data, list) else [])
        records.append({
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider": provider_name, "capability": capability, "reason": reason,
        })
        _save(_AUDIT_LOG, {"schema_version": 1, "records": records[-500:]})
    except Exception:
        pass  # audit failure must never block routing


def filter_candidates(candidates: list, capability: str,
                      production: bool = True) -> tuple:
    """Split candidates into (admissible, denials). denials is a list of
    ai_router attempt dicts for the caller's structured failure log."""
    admissible, denials = [], []
    for name in candidates:
        reason = check(name, capability, production=production)
        if reason is None:
            admissible.append(name)
        else:
            denials.append({"provider": name, "error_type": "policy_denied",
                            "error": reason})
    return admissible, denials
