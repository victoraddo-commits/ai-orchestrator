import json

import pytest

import core.security_scanner as scanner
from core.sandbox import SandboxUnavailable


def _fake_result(stdout="", stderr="", exit_code=0):
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timed_out": False}


def test_run_bandit_skips_when_no_python_files(tmp_path):
    (tmp_path / "index.js").write_text("console.log('hi')")

    result = scanner.run_bandit(str(tmp_path))

    assert result["available"] is False
    assert result["ran"] is False
    assert result["findings"] == []


def test_run_bandit_parses_findings(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("import os")

    bandit_output = json.dumps({
        "results": [
            {
                "issue_severity": "HIGH",
                "test_name": "hardcoded_password",
                "filename": "app.py",
                "line_number": 3,
                "issue_text": "Possible hardcoded password",
            }
        ]
    })

    monkeypatch.setattr(scanner, "run_in_sandbox", lambda *a, **k: _fake_result(stdout=bandit_output))

    result = scanner.run_bandit(str(tmp_path))

    assert result["ran"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["file"] == "app.py"
    assert result["findings"][0]["line"] == 3


def test_run_bandit_handles_unparseable_output(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("import os")

    monkeypatch.setattr(scanner, "run_in_sandbox", lambda *a, **k: _fake_result(stdout="not json", stderr="boom"))

    result = scanner.run_bandit(str(tmp_path))

    assert result["ran"] is False
    assert result["error"] is not None


def test_run_bandit_handles_sandbox_unavailable(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("import os")

    def raise_unavailable(*a, **k):
        raise SandboxUnavailable("docker not reachable")

    monkeypatch.setattr(scanner, "run_in_sandbox", raise_unavailable)

    result = scanner.run_bandit(str(tmp_path))

    assert result["ran"] is False
    assert "docker not reachable" in result["error"]


def test_run_npm_audit_skips_when_no_package_json(tmp_path):
    result = scanner.run_npm_audit(str(tmp_path))

    assert result["available"] is False
    assert result["ran"] is False


def test_run_npm_audit_parses_vulnerabilities(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}')

    audit_output = json.dumps({
        "vulnerabilities": {
            "lodash": {"name": "lodash", "severity": "critical"}
        }
    })

    monkeypatch.setattr(scanner, "run_in_sandbox", lambda *a, **k: _fake_result(stdout=audit_output))

    result = scanner.run_npm_audit(str(tmp_path))

    assert result["ran"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["severity"] == "critical"


def test_run_semgrep_parses_findings(tmp_path, monkeypatch):
    semgrep_output = json.dumps({
        "results": [
            {
                "check_id": "python.lang.security.audit.eval-detected",
                "path": "app.py",
                "start": {"line": 10},
                "extra": {"severity": "ERROR", "message": "eval() detected"},
            }
        ]
    })

    monkeypatch.setattr(scanner, "run_in_sandbox", lambda *a, **k: _fake_result(stdout=semgrep_output))

    result = scanner.run_semgrep(str(tmp_path))

    assert result["ran"] is True
    assert result["findings"][0]["line"] == 10
    assert result["findings"][0]["title"] == "python.lang.security.audit.eval-detected"


def test_run_trivy_parses_vulnerabilities(tmp_path, monkeypatch):
    trivy_output = json.dumps({
        "Results": [
            {
                "Target": "requirements.txt",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-0001",
                        "Severity": "HIGH",
                        "Title": "Some vuln",
                        "PkgName": "requests",
                    }
                ],
            }
        ]
    })

    monkeypatch.setattr(scanner, "run_in_sandbox", lambda *a, **k: _fake_result(stdout=trivy_output))

    result = scanner.run_trivy(str(tmp_path))

    assert result["ran"] is True
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["file"] == "requirements.txt"


def test_run_trivy_handles_sandbox_unavailable(tmp_path, monkeypatch):
    def raise_unavailable(*a, **k):
        raise SandboxUnavailable("docker not reachable")

    monkeypatch.setattr(scanner, "run_in_sandbox", raise_unavailable)

    result = scanner.run_trivy(str(tmp_path))

    assert result["ran"] is False
    assert result["error"] is not None


def test_run_all_scans_aggregates_and_never_raises(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("import os")
    (tmp_path / "package.json").write_text('{"name": "x"}')

    def raise_unavailable(*a, **k):
        raise SandboxUnavailable("docker not reachable")

    monkeypatch.setattr(scanner, "run_in_sandbox", raise_unavailable)

    report = scanner.run_all_scans(str(tmp_path))

    assert set(report["scanners"]) == {"bandit", "semgrep", "npm_audit", "trivy"}
    assert report["total_findings"] == 0
    assert report["highest_severity"] is None


def test_run_all_scans_computes_highest_severity(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("import os")

    def fake_run_in_sandbox(project_path, command, **kwargs):
        if "bandit" in command:
            return _fake_result(stdout=json.dumps({
                "results": [{"issue_severity": "MEDIUM", "test_name": "t", "filename": "app.py",
                             "line_number": 1, "issue_text": "d"}]
            }))
        if "semgrep" in command:
            return _fake_result(stdout=json.dumps({
                "results": [{"check_id": "c", "path": "app.py", "start": {"line": 1},
                             "extra": {"severity": "ERROR", "message": "critical-ish"}}]
            }))
        return _fake_result(stdout="{}")

    monkeypatch.setattr(scanner, "run_in_sandbox", fake_run_in_sandbox)

    report = scanner.run_all_scans(str(tmp_path))

    assert report["total_findings"] >= 1
    assert report["highest_severity"] is not None


@pytest.mark.integration
def test_run_all_scans_against_a_real_vulnerable_fixture(tmp_path):
    # Deliberately insecure fixture -- this is exactly the pattern the
    # scanners under test (Bandit/Semgrep) are supposed to catch, not code
    # this project runs. Never executed; only written to disk for the
    # sandboxed scanners to analyze statically.
    (tmp_path / "app.py").write_text(
        "import subprocess\n"
        "PASSWORD = 'hunter2'\n"
        "def run(cmd):\n"
        "    subprocess.call(cmd, shell=True)\n"
    )
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "vuln-fixture",
        "version": "1.0.0",
        "dependencies": {"lodash": "4.17.4"},
    }))

    report = scanner.run_all_scans(str(tmp_path))

    for name, result in report["scanners"].items():
        assert result["error"] is None or "not available" not in (result["error"] or ""), (
            f"{name} failed unexpectedly: {result['error']}"
        )

    assert report["total_findings"] > 0
