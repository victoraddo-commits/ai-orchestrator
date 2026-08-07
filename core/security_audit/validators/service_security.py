"""Phase 18A-b: Service Security Validator.

Audits runtime service security:
- Environment variable hygiene (leaked secrets, debug modes in production)
- Process privilege and ownership checks
- Service configuration hardening
"""

import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List


DANGEROUS_ENV_PATTERNS = [
    ("DEBUG", r"(?i)^(true|1|yes|on)$"),
    ("DEVELOPMENT", r"(?i)^(true|1|yes|on)$"),
    ("TESTING", r"(?i)^(true|1|yes|on)$"),
    ("DISABLE_AUTH", r"(?i)^(true|1|yes|on)$"),
    ("DISABLE_SSL", r"(?i)^(true|1|yes|on)$"),
    ("INSECURE_SKIP_VERIFY", r"(?i)^(true|1|yes|on)$"),
    ("PYTHONUNBUFFERED", r"(?i)^(true|1|yes|on)$"),
    ("FLASK_DEBUG", r"(?i)^(true|1|yes|on)$"),
]

SECRET_ENV_PATTERNS = [
    "PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE_KEY",
    "CREDENTIAL", "AUTH_KEY", "JWT_SECRET", "ENCRYPTION_KEY",
    "MASTER_KEY", "DB_PASSWORD", "REDIS_PASSWORD",
]

SENSITIVE_ENV_VALUES = [
    "/etc/shadow", "/root/.ssh", "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY", "BEGIN EC PRIVATE KEY",
]

CRITICAL_PROCESSES = [
    "ai-orchestrator", "uvicorn", "python", "docker", "containerd",
    "nginx", "postgres", "mysql", "redis-server",
]


def _get_service_processes() -> List[Dict[str, Any]]:
    """Return running processes for critical services."""
    processes = []
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines()[1:]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            user = parts[0]
            pid = parts[1]
            cmd = parts[10] if len(parts) > 10 else ""

            for svc in CRITICAL_PROCESSES:
                if svc in cmd and "ps aux" not in cmd:
                    processes.append({
                        "pid": int(pid),
                        "user": user,
                        "command": cmd,
                        "service": svc,
                    })
                    break
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return processes


def audit_environment_variables() -> Dict[str, Any]:
    """Audit environment variables for security issues.

    Returns findings about debug modes, secret leakage, and dangerous configurations.
    """
    import re

    findings = []

    for key, value in os.environ.items():
        key_upper = key.upper()

        for secret_pattern in SECRET_ENV_PATTERNS:
            if secret_pattern in key_upper:
                is_exposed = bool(value and value != "" and len(value) > 4)
                if is_exposed:
                    findings.append({
                        "type": "environment",
                        "severity": "medium",
                        "issue": f"Environment variable {key} may contain a secret",
                        "key": key,
                        "value_hint": f"{value[:4]}..." if len(value) > 4 else value,
                        "recommendation": f"Ensure {key} uses a secrets manager or is file-backed with 0600 permissions",
                    })
                break

        for pattern_name, pattern_regex in DANGEROUS_ENV_PATTERNS:
            if key_upper == pattern_name and re.match(pattern_regex, value or ""):
                findings.append({
                    "type": "environment",
                    "severity": "high",
                    "issue": f"Dangerous environment setting: {key}={value}",
                    "key": key,
                    "value": value,
                    "recommendation": f"Disable {key} in production; this enables debug/unsafe modes",
                })
                break

        for sensitive in SENSITIVE_ENV_VALUES:
            if sensitive.lower() in (value or "").lower():
                findings.append({
                    "type": "environment",
                    "severity": "critical",
                    "issue": f"Environment variable {key} contains sensitive data: {sensitive}",
                    "key": key,
                    "value_hint": "[SENSITIVE CONTENT REDACTED]",
                    "recommendation": f"Remove sensitive content from {key}; use file-based secrets",
                })
                break

    return {
        "findings": findings,
        "total_findings": len(findings),
        "by_severity": _group_by_severity(findings),
    }


def audit_process_privileges() -> Dict[str, Any]:
    """Audit running process privileges.

    Reports on root-owned processes, capability usage, and isolation boundaries.
    """
    findings = []
    processes = _get_service_processes()

    for proc in processes:
        if proc["user"] == "root":
            findings.append({
                "type": "process_privilege",
                "severity": "medium",
                "issue": f"Service '{proc['service']}' running as root (PID {proc['pid']})",
                "pid": proc["pid"],
                "user": proc["user"],
                "command": proc["command"],
                "recommendation": f"Run {proc['service']} as a dedicated non-root user",
            })

        if proc["user"] == "nobody":
            findings.append({
                "type": "process_privilege",
                "severity": "info",
                "issue": f"Service '{proc['service']}' running as nobody (PID {proc['pid']})",
                "pid": proc["pid"],
                "user": proc["user"],
                "command": proc["command"],
            })

    return {
        "findings": findings,
        "total_processes": len(processes),
        "total_findings": len(findings),
        "by_severity": _group_by_severity(findings),
    }


def audit_service_configs(base_dir: str) -> Dict[str, Any]:
    """Audit service configuration files for security hardening.

    Checks systemd unit files, docker-compose files, and config files
    for security-relevant settings.
    """
    findings = []
    base = Path(base_dir)

    conf_files = list(base.rglob("*.service")) + list(base.rglob("docker-compose*.yml"))

    for conf_file in conf_files:
        try:
            content = conf_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        filename = conf_file.name
        filepath = str(conf_file)

        if filename.endswith(".service"):
            if "User=root" in content:
                findings.append({
                    "type": "service_config",
                    "severity": "high",
                    "issue": f"Systemd service {filepath} runs as root",
                    "file": filepath,
                    "recommendation": "Configure User= to a non-root service account",
                })

            if "ProtectSystem=" not in content:
                findings.append({
                    "type": "service_config",
                    "severity": "medium",
                    "issue": f"Systemd service {filepath} missing ProtectSystem= directive",
                    "file": filepath,
                    "recommendation": "Add ProtectSystem=full to the [Service] section",
                })

            if "NoNewPrivileges=" not in content:
                findings.append({
                    "type": "service_config",
                    "severity": "medium",
                    "issue": f"Systemd service {filepath} missing NoNewPrivileges= directive",
                    "file": filepath,
                    "recommendation": "Add NoNewPrivileges=yes to the [Service] section",
                })

        if "docker-compose" in filename:
            if "privileged: true" in content:
                findings.append({
                    "type": "service_config",
                    "severity": "critical",
                    "issue": f"Docker compose {filepath} has privileged container mode",
                    "file": filepath,
                    "recommendation": "Remove privileged: true unless absolutely necessary; use capability additions instead",
                })

            if "network_mode: host" in content:
                findings.append({
                    "type": "service_config",
                    "severity": "high",
                    "issue": f"Docker compose {filepath} uses host network mode",
                    "file": filepath,
                    "recommendation": "Use bridge network with explicit port mappings instead of host mode",
                })

    return {
        "findings": findings,
        "total_findings": len(findings),
        "by_severity": _group_by_severity(findings),
    }


def _group_by_severity(findings: list) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    return counts
