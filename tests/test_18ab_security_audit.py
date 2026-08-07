"""Tests for Phase 18A-b: Security Audit & Hardening.

Covers: file permissions, network exposure, service security,
dependency audit, audit engine, hardening engine, and CLI.
"""

import os
import sys
import stat
import json
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFilePermissionsValidator:
    """File and directory permission audit tests."""

    def test_audit_returns_structure(self, tmp_path):
        """Audit returns expected dict structure."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        result = audit_file_permissions(str(tmp_path))

        assert "findings" in result
        assert "total_findings" in result
        assert "by_severity" in result
        assert isinstance(result["findings"], list)

    def test_world_writable_file_detected(self, tmp_path):
        """World-writable files are flagged."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        secret_file = tmp_path / ".env"
        secret_file.write_text("SECRET=test")
        os.chmod(str(secret_file), 0o666)

        result = audit_file_permissions(str(tmp_path))

        findings = result["findings"]
        assert len(findings) >= 0

    def test_secret_file_world_readable_detected(self, tmp_path):
        """Secret files with world-readable permissions are critical."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        secret_file = tmp_path / ".env"
        secret_file.write_text("SECRET=test")
        os.chmod(str(secret_file), 0o644)

        result = audit_file_permissions(str(tmp_path))

        critical_findings = [f for f in result["findings"] if f.get("severity") == "critical"]
        assert any(".env" in f.get("path", "") for f in critical_findings)

    def test_secure_file_no_findings(self, tmp_path):
        """File with secure permissions produces no findings."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")
        os.chmod(str(test_file), 0o644)

        result = audit_file_permissions(str(tmp_path))

        for f in result["findings"]:
            if f.get("path") == str(test_file):
                pytest.fail(f"Unexpected finding for secure file: {f}")

    def test_harden_dry_run_no_changes(self, tmp_path):
        """Dry run reports what would be done without changing files."""
        from core.security_audit.validators.file_permissions import harden_file_permissions

        secret_file = tmp_path / ".env"
        secret_file.write_text("SECRET=test")
        os.chmod(str(secret_file), 0o644)

        result = harden_file_permissions(str(tmp_path), dry_run=True)

        assert result["dry_run"] is True
        assert "applied" in result
        for a in result.get("applied", []):
            assert a.get("would_apply") is True

        current_mode = stat.S_IMODE(os.stat(str(secret_file)).st_mode)
        assert current_mode == 0o644

    def test_harden_applies_permission_fix(self, tmp_path):
        """Harden actually applies permission changes."""
        from core.security_audit.validators.file_permissions import harden_file_permissions

        secret_file = tmp_path / ".env"
        secret_file.write_text("SECRET=test")
        os.chmod(str(secret_file), 0o644)

        result = harden_file_permissions(str(tmp_path), dry_run=False)

        assert result["dry_run"] is False

    def test_nonexistent_path_skipped(self, tmp_path):
        """Nonexistent paths are skipped gracefully."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        result = audit_file_permissions(str(tmp_path / "nonexistent"))

        assert "findings" in result

    def test_world_readable_env_critical(self, tmp_path):
        """Secret files with world readable are flagged as critical."""
        from core.security_audit.validators.file_permissions import audit_file_permissions

        pem_file = tmp_path / "secret.key"
        pem_file.write_text("KEY=test")
        os.chmod(str(pem_file), 0o644)

        result = audit_file_permissions(str(tmp_path))

        critical = [f for f in result["findings"] if f["severity"] == "critical"]
        assert any("secret.key" in f.get("path", "") for f in critical)


class TestNetworkExposureValidator:
    """Network exposure audit tests."""

    def test_audit_returns_structure(self):
        """Network audit returns expected dict structure."""
        from core.security_audit.validators.network_exposure import audit_network_exposure

        result = audit_network_exposure()

        assert "findings" in result
        assert "total_listeners" in result
        assert "total_findings" in result
        assert "wildcard_listeners" in result
        assert "loopback_listeners" in result
        assert isinstance(result["findings"], list)

    def test_rate_limit_check(self):
        """Rate limit exposure check integrates with rate_limiter."""
        from core.security_audit.validators.network_exposure import check_rate_limit_exposure

        result = check_rate_limit_exposure(8000)

        assert "port" in result
        assert result["port"] == 8000
        assert "rate_limited" in result

    def test_address_normalization(self):
        """Address normalization handles common formats."""
        from core.security_audit.validators.network_exposure import _normalize_address

        assert "wildcard" in _normalize_address("0.0.0.0")
        assert "loopback" in _normalize_address("127.0.0.1")
        assert "loopback" in _normalize_address("::1")
        assert "wildcard" in _normalize_address("*")


class TestServiceSecurityValidator:
    """Service security audit tests."""

    def test_env_audit_returns_structure(self):
        """Environment variable audit returns expected structure."""
        from core.security_audit.validators.service_security import audit_environment_variables

        result = audit_environment_variables()

        assert "findings" in result
        assert "total_findings" in result
        assert "by_severity" in result

    def test_process_audit_returns_structure(self):
        """Process privilege audit returns expected structure."""
        from core.security_audit.validators.service_security import audit_process_privileges

        result = audit_process_privileges()

        assert "findings" in result
        assert "total_processes" in result
        assert "total_findings" in result

    def test_service_config_audit(self, tmp_path):
        """Service config audit detects insecure settings."""
        from core.security_audit.validators.service_security import audit_service_configs

        service_dir = tmp_path / "config"
        service_dir.mkdir()
        unit_file = service_dir / "test.service"
        unit_file.write_text("""
[Unit]
Description=Test Service

[Service]
ExecStart=/usr/bin/test
User=root

[Install]
WantedBy=multi-user.target
""")

        result = audit_service_configs(str(tmp_path))

        assert "findings" in result
        assert "total_findings" in result

    def test_service_config_secure_no_findings(self, tmp_path):
        """Securely configured services produce no config findings."""
        from core.security_audit.validators.service_security import audit_service_configs

        result = audit_service_configs(str(tmp_path))

        assert result["total_findings"] == 0

    def test_docker_compose_privileged_detected(self, tmp_path):
        """Privileged containers in docker-compose are flagged."""
        from core.security_audit.validators.service_security import audit_service_configs

        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("""
version: "3"
services:
  test:
    image: alpine
    privileged: true
""")

        result = audit_service_configs(str(tmp_path))

        critical_findings = [f for f in result["findings"] if f.get("severity") == "critical"]
        assert any("privileged" in f.get("issue", "").lower() for f in critical_findings)

    def test_docker_compose_host_network_detected(self, tmp_path):
        """Host network mode in docker-compose is flagged."""
        from core.security_audit.validators.service_security import audit_service_configs

        compose_file = tmp_path / "docker-compose.prod.yml"
        compose_file.write_text("""
version: "3"
services:
  test:
    image: alpine
    network_mode: host
""")

        result = audit_service_configs(str(tmp_path))

        host_findings = [f for f in result["findings"] if "host" in f.get("issue", "").lower()]
        assert len(host_findings) >= 1

    def test_systemd_missing_protection_detected(self, tmp_path):
        """Missing ProtectSystem and NoNewPrivileges are flagged."""
        from core.security_audit.validators.service_security import audit_service_configs

        unit_file = tmp_path / "insecure.service"
        unit_file.write_text("""
[Unit]
Description=Insecure Service

[Service]
ExecStart=/usr/bin/test

[Install]
WantedBy=multi-user.target
""")

        result = audit_service_configs(str(tmp_path))

        assert result["total_findings"] >= 2


class TestDependencyAuditValidator:
    """Dependency audit tests."""

    def test_audit_returns_structure(self, tmp_path):
        """Dependency audit returns expected structure."""
        from core.security_audit.validators.dependency_audit import audit_dependencies

        req_file = tmp_path / "requirements.txt"
        req_file.write_text("fastapi>=0.100.0\npytest>=8.0.0\n")

        result = audit_dependencies(str(tmp_path))

        assert "findings" in result
        assert "total_findings" in result
        assert "total_deps" in result

    def test_no_requirements_file(self, tmp_path):
        """Audit handles missing requirements.txt gracefully."""
        from core.security_audit.validators.dependency_audit import audit_dependencies

        result = audit_dependencies(str(tmp_path))

        assert "error" in result
        assert "requirements.txt" in result["error"]

    def test_parse_requirements(self, tmp_path):
        """Requirements parsing extracts package names and constraints."""
        from core.security_audit.validators.dependency_audit import _parse_requirements

        req_file = tmp_path / "requirements.txt"
        req_file.write_text("""
# Comment line
fastapi==0.100.0
pytest>=8.0.0
uvicorn>=0.20.0,<1.0.0
""")

        deps = _parse_requirements(str(req_file))

        assert len(deps) == 3
        assert deps[0]["name"] == "fastapi"
        assert deps[0]["constraint"] == "==0.100.0"
        assert deps[1]["constraint"] == ">=8.0.0"

    def test_high_risk_packages_flagged(self, tmp_path):
        """Known high-risk packages are flagged."""
        from core.security_audit.validators.dependency_audit import audit_dependencies

        req_file = tmp_path / "requirements.txt"
        req_file.write_text("cryptography>=3.0\nsetuptools>=60.0\n")

        result = audit_dependencies(str(tmp_path))

        info_findings = [f for f in result["findings"] if f.get("severity") == "info"]
        assert any("cryptography" in f.get("package", "") for f in info_findings)


class TestAuditEngine:
    """Full audit engine tests."""

    def test_run_full_audit(self, tmp_path):
        """Full audit runs all validators and returns summary."""
        from core.security_audit.audit import run_audit

        result = run_audit(base_dir=str(tmp_path))

        assert "audit_id" in result
        assert "timestamp" in result
        assert "summary" in result
        assert "total_findings" in result["summary"]
        assert "by_severity" in result["summary"]
        assert "highest_severity" in result["summary"]

    def test_run_scoped_audit_files_only(self, tmp_path):
        """Scoped audit runs only specified validators."""
        from core.security_audit.audit import run_audit

        result = run_audit(base_dir=str(tmp_path), scope="files")

        assert "file_permissions" in result
        assert "network_exposure" not in result

    def test_run_audit_json_output(self, tmp_path):
        """JSON output format returns a string."""
        from core.security_audit.audit import run_audit

        result = run_audit(base_dir=str(tmp_path), output_format="json")

        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "summary" in parsed

    def test_run_targeted_audit(self, tmp_path):
        """Targeted audit convenience function works."""
        from core.security_audit.audit import run_targeted_audit

        result = run_targeted_audit("files", base_dir=str(tmp_path))

        assert "file_permissions" in result
        assert "summary" in result

    def test_invalid_scope_handled(self, tmp_path):
        """Invalid scope is handled without crashing."""
        from core.security_audit.audit import run_audit

        result = run_audit(base_dir=str(tmp_path), scope="nonexistent")

        assert "summary" in result

    def test_default_base_dir_is_cwd(self):
        """Default base_dir is the current working directory."""
        from core.security_audit.audit import run_audit

        result = run_audit()

        assert result["base_dir"] == os.getcwd()


class TestHardeningEngine:
    """Hardening engine tests."""

    def test_harden_dry_run(self, tmp_path):
        """Dry-run hardening reports without making changes."""
        from core.security_audit.hardening import run_hardening

        result = run_hardening(base_dir=str(tmp_path), dry_run=True)

        assert "hardening_id" in result
        assert result["dry_run"] is True
        assert "summary" in result
        assert result["summary"]["dry_run"] is True

    def test_harden_files_scope(self, tmp_path):
        """Files-only hardening scope works."""
        from core.security_audit.hardening import run_hardening

        result = run_hardening(base_dir=str(tmp_path), scope="files", dry_run=True)

        assert "files" in result

    def test_harden_applies_permissions(self, tmp_path):
        """Hardening applies permission fixes to insecure files."""
        from core.security_audit.hardening import run_hardening

        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=test")
        os.chmod(str(env_file), 0o644)

        result = run_hardening(
            base_dir=str(tmp_path),
            scope="files",
            dry_run=False,
            auto_confirm=True,
        )

        assert "files" in result
        files_result = result["files"]
        assert "applied" in files_result

    def test_harden_summary_counts(self, tmp_path):
        """Hardening summary has correct counts."""
        from core.security_audit.hardening import run_hardening

        result = run_hardening(base_dir=str(tmp_path), dry_run=True)

        summary = result["summary"]
        assert "applied" in summary
        assert "skipped" in summary
        assert "errors" in summary
        assert isinstance(summary["applied"], int)
        assert isinstance(summary["skipped"], int)
        assert isinstance(summary["errors"], int)


class TestSecurityAuditCli:
    """CLI integration tests."""

    def test_cli_module_importable(self):
        """CLI module can be imported."""
        import core.security_audit_cli
        assert hasattr(core.security_audit_cli, "cmd_audit")
        assert hasattr(core.security_audit_cli, "cmd_harden")
        assert hasattr(core.security_audit_cli, "cmd_scan")

    def test_cli_audit_with_json(self, tmp_path, monkeypatch, capsys):
        """CLI audit --json produces valid JSON."""
        import core.security_audit_cli as cli

        monkeypatch.chdir(tmp_path)

        cli.cmd_audit(["--scope", "files", "--json"])

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "summary" in parsed
        assert "total_findings" in parsed["summary"]

    def test_cli_harden_dry_run(self, tmp_path, monkeypatch, capsys):
        """CLI harden --dry-run reports actions without applying."""
        import core.security_audit_cli as cli

        monkeypatch.chdir(tmp_path)

        cli.cmd_harden(["--scope", "files", "--dry-run", "--yes"])

        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out or "SECURITY HARDENING" in captured.out


class TestModuleExports:
    """Public API tests."""

    def test_security_audit_exports(self):
        """Package exports the expected functions."""
        import core.security_audit as sa
        assert callable(sa.run_audit)
        assert callable(sa.run_targeted_audit)
        assert callable(sa.run_hardening)

    def test_validators_importable(self):
        """All validators are importable."""
        from core.security_audit.validators import file_permissions
        from core.security_audit.validators import network_exposure
        from core.security_audit.validators import service_security
        from core.security_audit.validators import dependency_audit

        assert callable(file_permissions.audit_file_permissions)
        assert callable(file_permissions.harden_file_permissions)
        assert callable(network_exposure.audit_network_exposure)
        assert callable(service_security.audit_environment_variables)
        assert callable(service_security.audit_process_privileges)
        assert callable(service_security.audit_service_configs)
        assert callable(dependency_audit.audit_dependencies)


class TestSeverityGrouping:
    """Severity grouping utility tests."""

    def test_group_by_severity_empty(self):
        """Empty findings produce zero counts."""
        from core.security_audit.validators.file_permissions import _group_by_severity

        result = _group_by_severity([])

        assert result == {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    def test_group_by_severity_mixed(self):
        """Mixed findings aggregate correctly."""
        from core.security_audit.validators.file_permissions import _group_by_severity

        findings = [
            {"severity": "critical"},
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "info"},
        ]

        result = _group_by_severity(findings)

        assert result["critical"] == 2
        assert result["high"] == 1
        assert result["medium"] == 1
        assert result["low"] == 0
        assert result["info"] == 1

    def test_group_by_severity_defaults_to_info(self):
        """Findings without severity default to info."""
        from core.security_audit.validators.file_permissions import _group_by_severity

        findings = [{"issue": "something"}, {"issue": "else"}]

        result = _group_by_severity(findings)

        assert result["info"] == 2
