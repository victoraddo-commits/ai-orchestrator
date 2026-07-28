import json
import os

from core.sandbox import run_in_sandbox, SandboxUnavailable


PYTHON_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-bookworm-slim"
TRIVY_IMAGE = "aquasec/trivy:latest"

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info", "unknown"]

IGNORED_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}


def _has_python_files(project_path):
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        if any(f.endswith(".py") for f in files):
            return True
    return False


def _has_package_json(project_path):
    return os.path.exists(os.path.join(project_path, "package.json"))


def _skip(tool, reason):
    return {"tool": tool, "available": False, "ran": False, "findings": [], "error": reason}


def _failure(tool, error):
    return {"tool": tool, "available": True, "ran": False, "findings": [], "error": str(error)}


def _success(tool, findings):
    return {"tool": tool, "available": True, "ran": True, "findings": findings, "error": None}


def _run_scan(tool, project_path, command, image, timeout, entrypoint=None, memory=None):
    # pip installs console scripts to $HOME/.local/bin (system site-packages
    # is unwritable under the sandbox's read-only root fs), which isn't on
    # PATH by default -- confirmed live ("bandit: not found" despite a
    # successful install). Every pip-install-then-run scanner command needs this.
    command = f'export PATH="/root/.local/bin:$PATH"; {command}'

    kwargs = {"image": image, "network": True, "timeout": timeout, "entrypoint": entrypoint}
    if memory:
        kwargs["memory"] = memory

    try:
        result = run_in_sandbox(project_path, command, **kwargs)
    except SandboxUnavailable as error:
        return None, _failure(tool, error)

    try:
        data = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        return None, _failure(tool, f"could not parse {tool} output: {(result['stderr'] or '')[:300]}")

    return data, None


def run_bandit(project_path):
    if not _has_python_files(project_path):
        return _skip("bandit", "no Python files found")

    data, failure = _run_scan(
        "bandit", project_path,
        "pip install --quiet bandit 2>/dev/null && bandit -r . -f json -q || true",
        image=PYTHON_IMAGE, timeout=180,
    )
    if failure:
        return failure

    findings = [
        {
            "tool": "bandit",
            "severity": item.get("issue_severity", "unknown").lower(),
            "title": item.get("test_name"),
            "file": item.get("filename"),
            "line": item.get("line_number"),
            "description": item.get("issue_text"),
        }
        for item in data.get("results", [])
    ]

    return _success("bandit", findings)


def run_npm_audit(project_path):
    if not _has_package_json(project_path):
        return _skip("npm_audit", "no package.json found")

    data, failure = _run_scan(
        "npm_audit", project_path,
        "npm install --package-lock-only --silent 2>/dev/null; npm audit --json || true",
        image=NODE_IMAGE, timeout=120,
    )
    if failure:
        return failure

    findings = [
        {
            "tool": "npm_audit",
            "severity": (vuln.get("severity") or "unknown").lower(),
            "title": f"{name}: {vuln.get('severity')} severity vulnerability",
            "file": "package.json",
            "line": None,
            "description": name,
        }
        for name, vuln in (data.get("vulnerabilities") or {}).items()
    ]

    return _success("npm_audit", findings)


def run_semgrep(project_path):
    data, failure = _run_scan(
        "semgrep", project_path,
        "pip install --quiet semgrep 2>/dev/null && semgrep --config=auto --json -q . || true",
        # semgrep's own dependency tree (opentelemetry, httpx, jsonschema,
        # semgrep-core...) is heavy enough to OOM under the default 512m --
        # confirmed live (`pip install` got SIGKILL'd mid-install at 512m).
        image=PYTHON_IMAGE, timeout=240, memory="1536m",
    )
    if failure:
        return failure

    findings = [
        {
            "tool": "semgrep",
            "severity": (item.get("extra", {}).get("severity") or "unknown").lower(),
            "title": item.get("check_id"),
            "file": item.get("path"),
            "line": (item.get("start") or {}).get("line"),
            "description": item.get("extra", {}).get("message"),
        }
        for item in data.get("results", [])
    ]

    return _success("semgrep", findings)


def run_trivy(project_path):
    data, failure = _run_scan(
        "trivy", project_path,
        "trivy fs --format json --quiet /workspace",
        image=TRIVY_IMAGE, timeout=180, entrypoint="/bin/sh",
    )
    if failure:
        return failure

    findings = []
    for target in data.get("Results", []) or []:
        for vuln in target.get("Vulnerabilities", []) or []:
            findings.append({
                "tool": "trivy",
                "severity": (vuln.get("Severity") or "unknown").lower(),
                "title": vuln.get("Title") or vuln.get("VulnerabilityID"),
                "file": target.get("Target"),
                "line": None,
                "description": f"{vuln.get('PkgName')}: {vuln.get('VulnerabilityID')}",
            })

    return _success("trivy", findings)


def run_all_scans(project_path):
    scanners = {
        "bandit": run_bandit(project_path),
        "semgrep": run_semgrep(project_path),
        "npm_audit": run_npm_audit(project_path),
        "trivy": run_trivy(project_path),
    }

    all_findings = [f for result in scanners.values() for f in result["findings"]]

    highest_severity = None
    for severity in SEVERITY_ORDER:
        if any(f["severity"] == severity for f in all_findings):
            highest_severity = severity
            break

    return {
        "scanners": scanners,
        "total_findings": len(all_findings),
        "highest_severity": highest_severity,
    }
