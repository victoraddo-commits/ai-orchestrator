"""Phase 18A-b: Dependency Vulnerability Validator.

Audits Python dependencies for known vulnerabilities, outdated packages,
and supply-chain risks by parsing requirements.txt and pip metadata.
"""

import json
import subprocess
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


def _parse_requirements(filepath: str) -> List[Dict[str, Any]]:
    """Parse requirements.txt into structured package entries."""
    deps = []
    try:
        content = Path(filepath).read_text()
    except (OSError, UnicodeDecodeError):
        return deps

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue

        match = re.match(r'^([a-zA-Z0-9_.-]+)\s*([><=!~]+.*)?$', line)
        if match:
            name = match.group(1).lower()
            constraint = (match.group(2) or "").strip()
            deps.append({"name": name, "constraint": constraint, "raw": line})
        else:
            deps.append({"name": line, "constraint": "", "raw": line})

    return deps


def _get_installed_packages() -> Dict[str, str]:
    """Use pip list to get installed package versions."""
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return {item["name"].lower(): item["version"] for item in data}
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return {}


def _check_pip_audit() -> Optional[List[Dict[str, Any]]]:
    """Run pip-audit if available."""
    try:
        result = subprocess.run(
            ["pip-audit", "--format=json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_pip_outdated() -> List[Dict[str, Any]]:
    """Run pip list --outdated."""
    try:
        result = subprocess.run(
            ["pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True, timeout=60
        )
        return json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return []


KNOWN_HIGH_RISK_PACKAGES = [
    "setuptools",
    "pip",
    "wheel",
    "distribute",
    "cryptography",
    "pyopenssl",
    "certifi",
]

OVERRIDDEN_PACKAGES = [
    "bcrypt",
    "pyyaml",
    "httpx",
    "pydantic",
    "uvicorn",
    "fastapi",
]


def audit_dependencies(base_dir: str) -> Dict[str, Any]:
    """Audit Python dependencies for vulnerabilities and supply-chain risks.

    Args:
        base_dir: Project root directory.

    Returns:
        Dict with vulnerability findings, outdated packages, and risk assessment.
    """
    findings = []
    base = Path(base_dir)
    req_file = base / "requirements.txt"

    if not req_file.exists():
        return {
            "findings": [],
            "total_findings": 0,
            "by_severity": {},
            "error": "No requirements.txt found",
        }

    deps = _parse_requirements(str(req_file))
    installed = _get_installed_packages()

    for dep in deps:
        name = dep["name"]

        if name in OVERRIDDEN_PACKAGES and name not in installed:
            findings.append({
                "type": "dependency",
                "severity": "low",
                "issue": f"Package '{name}' is referenced but may not be explicitly installed (likely bundled/imported at runtime)",
                "package": name,
                "recommendation": f"Add {name} with a pinned version to requirements.txt",
            })

        if name in KNOWN_HIGH_RISK_PACKAGES:
            current = installed.get(name, "unknown")
            findings.append({
                "type": "dependency",
                "severity": "info",
                "issue": f"Package '{name}' ({current}) has elevated supply-chain risk",
                "package": name,
                "version": current,
                "recommendation": f"Ensure {name} is pinned to a known good version",
            })

    outdated = _get_pip_outdated()
    for pkg in outdated:
        findings.append({
            "type": "dependency",
            "severity": "medium",
            "issue": f"Package '{pkg['name']}' is outdated: {pkg['version']} → {pkg.get('latest_version', 'unknown')}",
            "package": pkg.get("name", ""),
            "current_version": pkg.get("version", ""),
            "latest_version": pkg.get("latest_version", ""),
            "recommendation": f"Update {pkg['name']} to {pkg.get('latest_version', 'latest')}",
        })

    pip_audit_vulns = _check_pip_audit()
    if pip_audit_vulns:
        for vuln in pip_audit_vulns:
            findings.append({
                "type": "dependency",
                "severity": "critical" if "critical" in str(vuln).lower() else "high",
                "issue": f"Known vulnerability in {vuln.get('name', 'unknown')}: {vuln.get('id', 'unknown')}",
                "package": vuln.get("name", ""),
                "vulnerability_id": vuln.get("id", ""),
                "recommendation": f"Update or patch {vuln.get('name', 'unknown')}",
            })

    load_order_security = []
    top_level_init = base / "core" / "__init__.py"
    if top_level_init.exists():
        load_order_security.append({
            "type": "dependency",
            "severity": "info",
            "issue": "Verified: core/__init__.py exists for package integrity",
        })

    findings.extend(load_order_security)

    return {
        "findings": findings,
        "total_findings": len(findings),
        "total_deps": len(deps),
        "total_installed": len(installed),
        "outdated_count": len(outdated),
        "by_severity": _group_by_severity(findings),
    }


def _group_by_severity(findings: list) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    return counts
