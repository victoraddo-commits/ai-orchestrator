"""Phase 18A-b: File Permission Validator.

Audits critical application paths for insecure file permissions:
- World-writable files and directories
- Insecure ownership patterns
- Config files with excessive permissions
- Secrets files lacking restrictive modes
"""

import os
import stat
import pwd
from pathlib import Path
from typing import Dict, Any, List, Optional


class FilePermissionsViolation(RuntimeError):
    """Raised when a file permissions hardening operation fails."""


CRITICAL_PATHS = [
    ".env",
    "memory/",
    "config/",
    "logs/",
]

SECURE_FILE_MODE = 0o600
SECURE_DIR_MODE = 0o700
RESTRICTED_DIR_MODE = 0o750

SECRET_EXTENSIONS = {".key", ".pem", ".crt", ".cert", ".env"}
SECRET_NAME_PATTERNS = ["secret", "private", "credential", "password", "token"]


def _get_owner(filepath: str) -> Optional[str]:
    try:
        st = os.stat(filepath)
        return pwd.getpwuid(st.st_uid).pw_name
    except (OSError, KeyError):
        return None


def _get_perms_octal(filepath: str) -> Optional[int]:
    try:
        return stat.S_IMODE(os.stat(filepath).st_mode)
    except OSError:
        return None


def _is_world_writable(mode: int) -> bool:
    return bool(mode & stat.S_IWOTH)


def _is_world_readable(mode: int) -> bool:
    return bool(mode & stat.S_IROTH)


def _is_group_writable(mode: int) -> bool:
    return bool(mode & stat.S_IWGRP)


def _collect_files(base_dir: str, patterns: List[str]) -> List[str]:
    base = Path(base_dir)
    files = []
    for pattern in ["*.py", "*.json", "*.yaml", "*.yml", "*.cfg", "*.conf",
                     "*.ini", "*.env", "*.key", "*.pem", "*.cert", "*.crt"]:
        for p in base.rglob(pattern):
            if not any(part in {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"}
                       for part in p.parts):
                files.append(str(p))
    return files


def audit_file_permissions(base_dir: str) -> Dict[str, Any]:
    """Audit file and directory permissions within the project.

    Returns a dict with findings and summary.
    """
    findings = []
    base = Path(base_dir)

    for rel_path in CRITICAL_PATHS:
        full_path = base / rel_path
        if not full_path.exists():
            continue

        if full_path.is_dir():
            _audit_directory(str(full_path), findings)
        else:
            _audit_single_file(str(full_path), findings, is_secret=".env" in rel_path or ".key" in rel_path or ".pem" in rel_path)

    for filepath in _collect_files(base_dir, CRITICAL_PATHS):
        ext = Path(filepath).suffix.lower()
        basename_lower = Path(filepath).name.lower()
        is_secret = (
            ext in SECRET_EXTENSIONS
            or any(p in basename_lower for p in SECRET_NAME_PATTERNS)
        )
        _audit_single_file(filepath, findings, is_secret=is_secret)

    for dirpath, dirnames, filenames in os.walk(str(base / "memory")):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            _audit_single_file(fp, findings)
        for dn in dirnames:
            dp = os.path.join(dirpath, dn)
            _audit_directory(dp, findings)

    return {
        "findings": findings,
        "total_findings": len(findings),
        "by_severity": _group_by_severity(findings),
    }


def _audit_single_file(filepath: str, findings: list, is_secret: bool = False) -> None:
    mode = _get_perms_octal(filepath)
    if mode is None:
        return

    expected_mode = SECURE_FILE_MODE if is_secret else 0o644
    owner = _get_owner(filepath)

    if is_secret and _is_world_readable(mode):
        findings.append({
            "path": filepath,
            "type": "file_permission",
            "severity": "critical",
            "issue": "Secret file is world-readable",
            "current_mode": f"{mode:04o}",
            "expected_mode": f"{expected_mode:04o}",
            "owner": owner,
            "fixable": True,
            "fix_action": f"chmod {expected_mode:04o} {filepath}",
        })

    if _is_world_writable(mode):
        findings.append({
            "path": filepath,
            "type": "file_permission",
            "severity": "high",
            "issue": "File is world-writable",
            "current_mode": f"{mode:04o}",
            "expected_mode": f"{0o644:04o}",
            "owner": owner,
            "fixable": True,
            "fix_action": f"chmod 644 {filepath}",
        })

    if _is_group_writable(mode) and is_secret:
        findings.append({
            "path": filepath,
            "type": "file_permission",
            "severity": "high",
            "issue": "Secret file is group-writable",
            "current_mode": f"{mode:04o}",
            "expected_mode": f"{SECURE_FILE_MODE:04o}",
            "owner": owner,
            "fixable": True,
            "fix_action": f"chmod {SECURE_FILE_MODE:04o} {filepath}",
        })


def _audit_directory(dirpath: str, findings: list) -> None:
    mode = _get_perms_octal(dirpath)
    if mode is None:
        return

    owner = _get_owner(dirpath)

    if _is_world_writable(mode):
        findings.append({
            "path": dirpath,
            "type": "dir_permission",
            "severity": "high",
            "issue": "Directory is world-writable",
            "current_mode": f"{mode:04o}",
            "expected_mode": f"{RESTRICTED_DIR_MODE:04o}",
            "owner": owner,
            "fixable": True,
            "fix_action": f"chmod {RESTRICTED_DIR_MODE:04o} {dirpath}",
        })

    memory_dir = str(Path(dirpath).parent) if not dirpath.endswith("memory") else dirpath
    if "memory" in dirpath and mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
        findings.append({
            "path": dirpath,
            "type": "dir_permission",
            "severity": "medium",
            "issue": "Memory directory has world-accessible permissions",
            "current_mode": f"{mode:04o}",
            "expected_mode": f"{SECURE_DIR_MODE:04o}",
            "owner": owner,
            "fixable": True,
            "fix_action": f"chmod {SECURE_DIR_MODE:04o} {dirpath}",
        })


def _group_by_severity(findings: list) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def harden_file_permissions(base_dir: str, dry_run: bool = False) -> Dict[str, Any]:
    """Apply permission hardening fixes.

    Args:
        base_dir: Root directory to operate on.
        dry_run: If True, report what would be done without making changes.

    Returns:
        Dict with applied and skipped fixes.
    """
    audit_result = audit_file_permissions(base_dir)
    findings = audit_result["findings"]
    applied = []
    skipped = []
    errors = []

    for finding in findings:
        if not finding.get("fixable"):
            skipped.append({**finding, "reason": "not fixable"})
            continue

        filepath = finding["path"]
        expected_str = finding.get("expected_mode", "0644")
        try:
            expected_mode = int(expected_str, 8)
        except (ValueError, TypeError):
            errors.append({**finding, "reason": f"invalid expected mode: {expected_str}"})
            continue

        if dry_run:
            applied.append({**finding, "would_apply": True})
            continue

        try:
            os.chmod(filepath, expected_mode)
            applied.append({**finding, "applied": True})
        except OSError as e:
            errors.append({**finding, "reason": str(e)})

    return {
        "audit": audit_result,
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }
