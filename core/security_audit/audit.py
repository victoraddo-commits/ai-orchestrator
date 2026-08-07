"""Phase 18A-b: Security Audit Engine.

Orchestrates the full security audit suite, running all validators
and aggregating results into a unified report. Supports targeted
audits via --scope and JSON output for integration with dashboards.
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.security_audit.validators.file_permissions import audit_file_permissions
from core.security_audit.validators.network_exposure import audit_network_exposure
from core.security_audit.validators.service_security import (
    audit_environment_variables,
    audit_process_privileges,
    audit_service_configs,
)
from core.security_audit.validators.dependency_audit import audit_dependencies


SCOPE_MAP = {
    "files": lambda base_dir: {"file_permissions": audit_file_permissions(base_dir)},
    "network": lambda base_dir: {"network_exposure": audit_network_exposure()},
    "environment": lambda base_dir: {"environment_variables": audit_environment_variables()},
    "processes": lambda base_dir: {"process_privileges": audit_process_privileges()},
    "services": lambda base_dir: {"service_configs": audit_service_configs(base_dir)},
    "dependencies": lambda base_dir: {"dependencies": audit_dependencies(base_dir)},
}


def run_audit(
    base_dir: Optional[str] = None,
    scope: Optional[str] = None,
    output_format: str = "dict",
) -> Dict[str, Any]:
    """Run the full security audit suite.

    Args:
        base_dir: Project root directory. Defaults to current working directory.
        scope: Comma-separated validator scopes (e.g., "files,network").
               None runs all validators.
        output_format: "dict" for Python dict, "json" for JSON string.

    Returns:
        Full audit report dict, or JSON-serializable dict.
    """
    if base_dir is None:
        base_dir = os.getcwd()

    base_dir = str(Path(base_dir).resolve())

    results = {
        "audit_id": f"audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_dir": base_dir,
        "scope": scope or "all",
    }

    if scope:
        selected_scopes = [s.strip() for s in scope.split(",") if s.strip() in SCOPE_MAP]
    else:
        selected_scopes = list(SCOPE_MAP.keys())

    for scope_name in selected_scopes:
        try:
            scope_fn = SCOPE_MAP[scope_name]
            result = scope_fn(base_dir)
            results.update(result)
        except Exception as e:
            results[scope_name] = {"error": str(e)}

    total_findings = 0
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    for key, val in results.items():
        if isinstance(val, dict) and "findings" in val:
            total_findings += val.get("total_findings", 0)
            by_sev = val.get("by_severity", {})
            for sev, count in by_sev.items():
                severity_counts[sev] = severity_counts.get(sev, 0) + count

    results["summary"] = {
        "total_findings": total_findings,
        "by_severity": severity_counts,
        "highest_severity": _highest_severity(severity_counts),
    }

    if output_format == "json":
        return json.dumps(results, indent=2, default=str)

    return results


def run_targeted_audit(scope: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Run a targeted audit for a single validator scope.

    Convenience wrapper around run_audit with a single scope.
    """
    return run_audit(base_dir=base_dir, scope=scope)


def _highest_severity(counts: Dict[str, int]) -> Optional[str]:
    for sev in ["critical", "high", "medium", "low", "info"]:
        if counts.get(sev, 0) > 0:
            return sev
    return None
