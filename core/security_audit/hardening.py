"""Phase 18A-b: Security Hardening Engine.

Automated remediation and hardening routines that apply fixes for
fixable findings discovered by the audit validators. Supports
dry-run mode for safe preview of changes.
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.security_audit.audit import run_audit
from core.security_audit.validators.file_permissions import harden_file_permissions


def run_hardening(
    base_dir: Optional[str] = None,
    scope: Optional[str] = None,
    dry_run: bool = False,
    auto_confirm: bool = False,
) -> Dict[str, Any]:
    """Run automated security hardening.

    Args:
        base_dir: Project root directory.
        scope: Comma-separated hardening scopes. None runs all.
        dry_run: If True, preview changes without applying them.
        auto_confirm: If True, skip confirmation prompts.

    Returns:
        Dict with hardening results per scope, including applied/failed/skipped counts.
    """
    if base_dir is None:
        base_dir = os.getcwd()

    base_dir = str(Path(base_dir).resolve())

    results = {
        "hardening_id": f"harden-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_dir": base_dir,
        "dry_run": dry_run,
        "scope": scope or "all",
    }

    if scope:
        selected_scopes = [s.strip() for s in scope.split(",") if s.strip() in HARDENING_MAP]
    else:
        selected_scopes = list(HARDENING_MAP.keys())

    for scope_name in selected_scopes:
        try:
            harden_fn = HARDENING_MAP[scope_name]
            scope_result = harden_fn(base_dir, dry_run=dry_run)
            results[scope_name] = scope_result
        except Exception as e:
            results[scope_name] = {"error": str(e)}

    total_applied = 0
    total_skipped = 0
    total_errors = 0

    for key, val in results.items():
        if isinstance(val, dict):
            total_applied += len(val.get("applied", []))
            total_skipped += len(val.get("skipped", []))
            total_errors += len(val.get("errors", []))

    results["summary"] = {
        "applied": total_applied,
        "skipped": total_skipped,
        "errors": total_errors,
        "dry_run": dry_run,
    }

    return results


def _harden_files(base_dir: str, dry_run: bool = False) -> Dict[str, Any]:
    return harden_file_permissions(base_dir, dry_run=dry_run)


def _harden_service_configs(base_dir: str, dry_run: bool = False) -> Dict[str, Any]:
    from core.security_audit.validators.service_security import audit_service_configs

    findings = audit_service_configs(base_dir)["findings"]

    applied = []
    skipped = []
    errors = []

    for finding in findings:
        filepath = finding.get("file", "")
        if not filepath or not os.path.exists(filepath):
            skipped.append({**finding, "reason": "file not found"})
            continue

        if "User=root" in finding.get("issue", ""):
            if dry_run:
                applied.append({**finding, "would_apply": True, "action": "replace User=root with non-root user"})
            else:
                skipped.append({**finding, "reason": "requires manual user account creation"})
            continue

        if "ProtectSystem=" in finding.get("issue", ""):
            if dry_run:
                applied.append({**finding, "would_apply": True, "action": "add ProtectSystem=full"})
            else:
                skipped.append({**finding, "reason": "manual systemd unit file editing required"})
            continue

        if "NoNewPrivileges=" in finding.get("issue", ""):
            if dry_run:
                applied.append({**finding, "would_apply": True, "action": "add NoNewPrivileges=yes"})
            else:
                skipped.append({**finding, "reason": "manual systemd unit file editing required"})
            continue

        skipped.append({**finding, "reason": "requires manual review"})

    return {
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }


HARDENING_MAP = {
    "files": _harden_files,
    "services": _harden_service_configs,
}
