"""Tests for audit_logger.py event bus integration."""
import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_external_audit_event_writes_to_log(monkeypatch):
    """_handle_external_audit_event calls log_audit_event with correct data."""
    written = []
    def mock_log(event_type, operator, endpoint, method, status_code=200, details=None, client_ip="unknown", trace_id=None):
        written.append({
            "event_type": event_type,
            "operator": operator,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "details": details,
            "client_ip": client_ip,
            "trace_id": trace_id,
        })

    from core import audit_logger
    original = audit_logger.log_audit_event
    audit_logger.log_audit_event = mock_log

    try:
        audit_logger._handle_external_audit_event("audit.critical.test", {
            "payload": {
                "operator": "test-op",
                "action": "test_action",
                "resource": "test_resource",
                "result": "success",
                "ip": "127.0.0.1",
                "details": {},
                "trace_id": "trace-123",
            },
            "source": "test-source",
            "timestamp": 1234567890.0,
        })

        assert len(written) == 1
        assert written[0]["event_type"] == "external_audit"
        assert written[0]["operator"] == "test-op"
        assert written[0]["endpoint"] == "audit.critical.test"
        assert written[0]["method"] == "EVENT"
        assert written[0]["trace_id"] == "trace-123"
        assert written[0]["details"]["source"] == "test-source"
    finally:
        audit_logger.log_audit_event = original
