"""Tests for Phase 18A-b: Audit Logging, Security Headers, Input Validation."""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Use temp directory for audit logs during tests
TEST_AUDIT_DIR = Path(tempfile.gettempdir()) / "kai_audit_test"


class TestAuditLogger:
    """Audit logging with HMAC integrity verification."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Use test directory for audit logs."""
        import core.audit_logger as alog
        alog.AUDIT_LOG_FILE = str(TEST_AUDIT_DIR / "audit_log.jsonl")
        alog.AUDIT_LOG_DIR = str(TEST_AUDIT_DIR)
        # Reset HMAC key
        alog._audit_hmac_key = None
        TEST_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        # Remove existing test log
        logfile = Path(alog.AUDIT_LOG_FILE)
        if logfile.exists():
            logfile.unlink()
        yield
        if logfile.exists():
            logfile.unlink()

    def test_log_and_read_basic_event(self):
        """Write an audit event and read it back."""
        from core.audit_logger import log_audit_event, read_audit_log

        log_audit_event(
            event_type="auth.login",
            operator="testuser",
            endpoint="/dashboard/api/login",
            method="POST",
            status_code=200,
            client_ip="192.168.1.1",
        )

        events = read_audit_log(limit=10)
        assert len(events) == 1
        e = events[0]
        assert e["event_type"] == "auth.login"
        assert e["operator"] == "testuser"
        assert e["method"] == "POST"
        assert e["status_code"] == 200
        assert "timestamp" in e
        assert "_sig" in e

    def test_multiple_events(self):
        """Multiple events are all logged and readable."""
        from core.audit_logger import log_audit_event, read_audit_log

        for i in range(5):
            log_audit_event(
                event_type="build.create",
                operator=f"user{i}",
                endpoint="/builds",
                method="POST",
                status_code=201,
            )

        events = read_audit_log(limit=100)
        assert len(events) == 5

    def test_filter_by_event_type(self):
        """read_audit_log can filter by event_type."""
        from core.audit_logger import log_audit_event, read_audit_log

        log_audit_event("auth.login", "user1", "/login", "POST")
        log_audit_event("build.create", "user1", "/builds", "POST")
        log_audit_event("auth.login", "user2", "/login", "POST")

        login_events = read_audit_log(event_type="auth.login")
        assert len(login_events) == 2
        assert all(e["event_type"] == "auth.login" for e in login_events)

    def test_filter_by_operator(self):
        """read_audit_log can filter by operator."""
        from core.audit_logger import log_audit_event, read_audit_log

        log_audit_event("api.call", "admin", "/test", "GET")
        log_audit_event("api.call", "viewer1", "/test", "GET")

        admin_events = read_audit_log(operator="admin")
        assert len(admin_events) == 1
        assert admin_events[0]["operator"] == "admin"

    def test_sensitive_fields_redacted(self):
        """Passwords and tokens are redacted in audit details."""
        from core.audit_logger import log_audit_event, read_audit_log

        log_audit_event(
            "auth.login_failed",
            "testuser",
            "/dashboard/api/login",
            "POST",
            status_code=401,
            details={"username": "testuser", "password": "secret123", "ip": "10.0.0.1"},
        )

        events = read_audit_log()
        assert len(events) == 1
        assert events[0]["details"]["password"] == "[REDACTED]"
        assert events[0]["details"]["username"] == "testuser"

    def test_log_integrity_verification(self):
        """HMAC integrity verification works."""
        from core.audit_logger import log_audit_event, verify_log_integrity

        log_audit_event("test.event", "user1", "/test", "GET")
        result = verify_log_integrity()

        assert result["total"] == 1
        assert result["valid"] == 1
        assert result["invalid"] == 0

    def test_audit_stats(self):
        """Audit stats aggregate correctly."""
        from core.audit_logger import log_audit_event, get_audit_stats

        log_audit_event("auth.login", "u1", "/login", "POST")
        log_audit_event("build.create", "u1", "/builds", "POST")
        log_audit_event("build.create", "u2", "/builds", "POST")

        stats = get_audit_stats()
        assert stats["total_events"] == 3
        assert "auth.login" in stats["event_types"]
        assert "build.create" in stats["event_types"]


class TestSecurityHeaders:
    """Security headers middleware tests."""

    def test_default_headers_present(self):
        """All required security headers are in defaults."""
        from core.security_headers import SECURITY_HEADERS

        assert "Content-Security-Policy" in SECURITY_HEADERS
        assert "Strict-Transport-Security" in SECURITY_HEADERS
        assert "X-Content-Type-Options" in SECURITY_HEADERS
        assert "X-Frame-Options" in SECURITY_HEADERS
        assert "Referrer-Policy" in SECURITY_HEADERS
        assert "Permissions-Policy" in SECURITY_HEADERS
        assert "Cache-Control" in SECURITY_HEADERS

    def test_csp_is_restrictive(self):
        """CSP header restricts resources appropriately."""
        from core.security_headers import SECURITY_HEADERS

        csp = SECURITY_HEADERS["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_hsts_includes_subdomains(self):
        """HSTS header includes subdomain protection."""
        from core.security_headers import SECURITY_HEADERS

        hsts = SECURITY_HEADERS["Strict-Transport-Security"]
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts

    def test_dangerous_headers_removed(self):
        """Dangerous headers are in removal list."""
        from core.security_headers import HEADERS_TO_REMOVE

        assert "server" in HEADERS_TO_REMOVE
        assert "x-powered-by" in HEADERS_TO_REMOVE

    def test_x_frame_options_deny(self):
        """X-Frame-Options is set to DENY (clickjacking protection)."""
        from core.security_headers import SECURITY_HEADERS

        assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"


class TestInputValidator:
    """Input validation and sanitization tests."""

    def test_validate_string_basic(self):
        """Valid string passes validation."""
        from core.input_validator import validate_string

        result = validate_string("hello world", "message")
        assert result["valid"] is True
        assert result["value"] == "hello world"

    def test_validate_string_empty_rejected(self):
        """Empty string is rejected by default."""
        from core.input_validator import validate_string

        result = validate_string("", "name")
        assert result["valid"] is False

    def test_validate_string_empty_allowed(self):
        """Empty string passes when allow_empty=True."""
        from core.input_validator import validate_string

        result = validate_string("", "name", allow_empty=True)
        assert result["valid"] is True
        assert result["value"] == ""

    def test_validate_string_max_length(self):
        """String exceeding max_length is rejected."""
        from core.input_validator import validate_string

        result = validate_string("a" * 100, "short", max_length=10)
        assert result["valid"] is False
        assert "at most 10" in result["error"]

    def test_sql_injection_detected(self):
        """SQL injection patterns are detected."""
        from core.input_validator import contains_sql_injection

        assert contains_sql_injection("' OR 1=1 --") is True
        assert contains_sql_injection("DROP TABLE users") is True
        assert contains_sql_injection("SELECT * FROM users WHERE id = 1") is False  # Not injection

    def test_xss_detected(self):
        """XSS patterns are detected."""
        from core.input_validator import contains_xss

        assert contains_xss("<script>alert(1)</script>") is True
        assert contains_xss('<img onerror="alert(1)">') is True
        assert contains_xss("javascript:void(0)") is True
        assert contains_xss("Hello, world!") is False

    def test_validate_string_blocks_xss(self):
        """String validation rejects XSS when check_xss=True."""
        from core.input_validator import validate_string

        result = validate_string("<script>alert(1)</script>", "input", check_xss=True)
        assert result["valid"] is False

    def test_validate_string_blocks_sql(self):
        """String validation rejects SQL injection when check_sql=True."""
        from core.input_validator import validate_string

        result = validate_string("'; DROP TABLE users; --", "query", check_sql=True)
        assert result["valid"] is False

    def test_validate_dict_fields_required(self):
        """Required field validation catches missing fields."""
        from core.input_validator import validate_dict_fields

        result = validate_dict_fields(
            {"name": "test"},
            required=["name", "email"],
        )
        assert result["valid"] is False
        assert any("email" in e["field"] for e in result["errors"])

    def test_validate_dict_fields_unknown_rejected(self):
        """Unknown fields are rejected."""
        from core.input_validator import validate_dict_fields

        result = validate_dict_fields(
            {"name": "test", "hack": "evil"},
            required=["name"],
        )
        assert result["valid"] is False
        assert any("hack" in e["field"] for e in result["errors"])

    def test_validate_dict_fields_valid(self):
        """Valid dict passes all checks."""
        from core.input_validator import validate_dict_fields

        result = validate_dict_fields(
            {"name": "Alice", "email": "alice@example.com"},
            required=["name"],
            optional=["email"],
        )
        assert result["valid"] is True
        assert result["fields"]["name"] == "Alice"
        assert result["fields"]["email"] == "alice@example.com"

    def test_is_valid_email(self):
        """Email validation works correctly."""
        from core.input_validator import is_valid_email

        assert is_valid_email("user@example.com") is True
        assert is_valid_email("user+tag@domain.co.uk") is True
        assert is_valid_email("not-an-email") is False
        assert is_valid_email("") is False
        assert is_valid_email("user@") is False

    def test_is_valid_url(self):
        """URL validation works correctly."""
        from core.input_validator import is_valid_url

        assert is_valid_url("https://example.com") is True
        assert is_valid_url("http://localhost:8080/path") is True
        assert is_valid_url("ftp://files.com") is False  # Not HTTPS
        assert is_valid_url("not-a-url") is False

    def test_sanitize_html(self):
        """HTML sanitization escapes dangerous characters."""
        from core.input_validator import sanitize_html

        assert sanitize_html("<script>") == "&lt;script&gt;"
        assert sanitize_html('"test"') == "&quot;test&quot;"
        assert sanitize_html("a & b") == "a &amp; b"
        assert sanitize_html("hello") == "hello"

    def test_validate_string_none_rejected(self):
        """None input is rejected."""
        from core.input_validator import validate_string

        result = validate_string(None, "name")
        assert result["valid"] is False

    def test_valid_string_preserves_content(self):
        """Valid strings are returned trimmed but intact."""
        from core.input_validator import validate_string

        result = validate_string("  hello world  ", "msg")
        assert result["valid"] is True
        assert result["value"] == "hello world"
