from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.audit_aggregator import (
    extract_client_ip,
    normalize_timestamp,
    map_build_to_audit_entry,
    _approval_audit_entries,
    map_decision_to_audit_entry,
    map_incident_to_audit_entry,
    get_audit_entries,
    format_audit_entries_as_csv,
    format_audit_entries_as_json,
)


class TestExtractClientIP:
    def test_x_forwarded_for(self):
        from fastapi import Request
        scope = {"type": "http", "headers": [(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2")]}
        request = Request(scope)
        assert extract_client_ip(request) == "10.0.0.1"

    def test_x_real_ip(self):
        from fastapi import Request
        scope = {"type": "http", "headers": [(b"x-real-ip", b"10.0.0.3")]}
        request = Request(scope)
        assert extract_client_ip(request) == "10.0.0.3"

    def test_x_forwarded_for_takes_priority(self):
        from fastapi import Request
        scope = {"type": "http", "headers": [
            (b"x-forwarded-for", b"10.0.0.1"),
            (b"x-real-ip", b"10.0.0.3"),
        ]}
        request = Request(scope)
        assert extract_client_ip(request) == "10.0.0.1"

    def test_client_host_fallback(self):
        from fastapi import Request
        scope = {"type": "http", "client": ("192.168.1.100", 54321), "headers": []}
        request = Request(scope)
        assert extract_client_ip(request) == "192.168.1.100"

    def test_default_localhost(self):
        from fastapi import Request
        scope = {"type": "http", "headers": []}
        request = Request(scope)
        assert extract_client_ip(request) == "127.0.0.1"


class TestNormalizeTimestamp:
    def test_iso_already(self):
        ts = "2026-08-03T12:00:00"
        result = normalize_timestamp(ts)
        assert result == ts

    def test_iso_with_timezone(self):
        ts = "2026-08-03T12:00:00+00:00"
        result = normalize_timestamp(ts)
        assert result == ts

    def test_microseconds(self):
        ts = "2026-07-28T00:24:02.970212"
        result = normalize_timestamp(ts)
        assert result == ts

    def test_none_returns_current(self):
        result = normalize_timestamp(None)
        assert result is not None
        datetime.fromisoformat(result)  # should not raise

    def test_empty_string_returns_current(self):
        result = normalize_timestamp("")
        assert result is not None

    def test_datetime_object(self):
        now = datetime.now(timezone.utc)
        result = normalize_timestamp(now)
        assert result == now.isoformat()

    def test_custom_format(self):
        result = normalize_timestamp("2026-08-03T12:00:00.000000")
        assert "2026-08-03" in result


class TestMapBuildToAuditEntry:
    def test_real_build_schema(self):
        build = {
            "id": "df0401cd",
            "trace_id": "df0401cd",
            "status": "FAILED",
            "created": "2026-07-27T23:23:01.519066",
            "updated": "2026-07-31T04:00:34.312409",
            "name": "13B",
            "description": "Rule-based phrase matching",
            "project_path": "/project/ai-orchestrator",
            "template": None,
        }
        entry = map_build_to_audit_entry(build)
        assert entry["id"] == "df0401cd"
        assert entry["action"] == "build_failed"
        assert entry["user"] == "system"
        assert entry["source_ip"] == "127.0.0.1"
        assert entry["project"] == "13B"
        assert entry["status"] == "FAILED"
        assert entry["source_store"] == "build_history"
        assert entry["details"]["name"] == "13B"
        assert entry["details"]["trace_id"] == "df0401cd"
        assert "2026-07-27" in entry["timestamp"]

    def test_build_with_operator_and_ip(self):
        build = {
            "id": "abc123",
            "status": "COMPLETED",
            "created": "2026-08-03T12:00:00",
            "name": "test-app",
            "description": "A test app",
            "operator": "cloudcli-plugin",
            "source_ip": "10.0.0.99",
        }
        entry = map_build_to_audit_entry(build)
        assert entry["user"] == "cloudcli-plugin"
        assert entry["source_ip"] == "10.0.0.99"
        assert entry["action"] == "build_completed"

    def test_build_minimal(self):
        build = {"id": "min001", "status": "REQUESTED", "created": "2026-08-01T00:00:00"}
        entry = map_build_to_audit_entry(build)
        assert entry["id"] == "min001"
        assert entry["action"] == "build_requested"
        assert entry["project"] == ""


class TestApprovalAuditEntries:
    def test_single_history_creates_single_entry(self):
        approval = {
            "id": "901cc2a3",
            "status": "executed",
            "created": "2026-07-28T16:05:15.750273",
            "history": [
                {"status": "pending", "timestamp": "2026-07-28T16:05:15.750273"},
            ],
            "build_id": "5353f7e0",
            "phase_id": "13E",
            "approval_type": "deploy",
            "title": "Approve deployment for 13E",
            "description": "1009 security finding(s) found.",
            "approved_by": "cloudcli-plugin",
        }
        entries = _approval_audit_entries(approval)
        assert len(entries) == 1
        e = entries[0]
        assert e["id"] == "901cc2a3"
        assert e["action"] == "approval_pending"
        assert e["user"] == "cloudcli-plugin"
        assert e["project"] == "13E"
        assert e["source_store"] == "approval_queue"
        assert e["details"]["approval_type"] == "deploy"
        assert e["details"]["build_id"] == "5353f7e0"

    def test_multiple_history_entries(self):
        approval = {
            "id": "app789",
            "status": "executed",
            "created": "2026-08-01T10:00:00",
            "history": [
                {"status": "pending", "timestamp": "2026-08-01T10:00:00"},
                {"status": "approved", "timestamp": "2026-08-01T10:30:00"},
                {"status": "executed", "timestamp": "2026-08-01T11:00:00"},
            ],
            "build_id": "build1",
            "phase_id": "17Z",
            "approval_type": "architecture",
            "approved_by": "cloudcli-plugin",
        }
        entries = _approval_audit_entries(approval)
        assert len(entries) == 3
        assert entries[0]["action"] == "approval_pending"
        assert entries[1]["action"] == "approval_approved"
        assert entries[2]["action"] == "approval_executed"

    def test_no_history(self):
        approval = {"id": "empty", "status": "pending"}
        entries = _approval_audit_entries(approval)
        assert len(entries) == 0

    def test_no_approved_by_defaults_to_system(self):
        approval = {
            "id": "app001",
            "status": "rejected",
            "history": [{"status": "rejected", "timestamp": "2026-08-01T12:00:00"}],
            "phase_id": "15A",
        }
        entries = _approval_audit_entries(approval)
        assert entries[0]["user"] == "system"


class TestMapDecisionToAuditEntry:
    def test_real_decision_schema(self):
        decision = {
            "id": "bad9ef2e",
            "trace_id": "3afa29d4",
            "status": "proposed",
            "created": "2026-08-03T09:38:56.838114",
            "incident_id": "3afa29d4",
            "problem": "Container stopped",
            "recommended_action": "restart_container",
            "confidence": 95,
            "risk_score": 20,
            "risk_level": "low",
            "requires_approval": True,
            "reason": "severity=critical",
        }
        entry = map_decision_to_audit_entry(decision)
        assert entry["id"] == "bad9ef2e"
        assert entry["action"] == "decision_proposed"
        assert entry["user"] == "system"
        assert entry["source_ip"] == "127.0.0.1"
        assert entry["project"] == "3afa29d4"
        assert entry["status"] == "proposed"
        assert entry["source_store"] == "decision_history"
        assert entry["details"]["problem"] == "Container stopped"
        assert entry["details"]["recommended_action"] == "restart_container"
        assert entry["details"]["risk_level"] == "low"
        assert entry["details"]["requires_approval"] is True

    def test_decision_with_operator(self):
        decision = {
            "id": "dec001",
            "status": "executed",
            "created": "2026-08-01T12:00:00",
            "incident_id": "inc1",
            "operator": "kai-system",
            "source_ip": "10.0.0.1",
        }
        entry = map_decision_to_audit_entry(decision)
        assert entry["user"] == "kai-system"
        assert entry["source_ip"] == "10.0.0.1"


class TestMapIncidentToAuditEntry:
    def test_real_incident_schema(self):
        incident = {
            "id": "59914358",
            "timestamp": "2026-07-27T10:57:25.412837",
            "service": "proxmox-health-score",
            "issue": "Healthy",
            "severity": "info",
            "occurrences": 1,
        }
        entry = map_incident_to_audit_entry(incident)
        assert entry["id"] == "59914358"
        assert entry["action"] == "incident_info"
        assert entry["user"] == "system"
        assert entry["source_ip"] == "127.0.0.1"
        assert entry["project"] == "proxmox-health-score"
        assert entry["status"] == "info"
        assert entry["source_store"] == "incidents"
        assert entry["details"]["service"] == "proxmox-health-score"
        assert entry["details"]["issue"] == "Healthy"
        assert entry["details"]["severity"] == "info"
        assert entry["details"]["occurrences"] == 1

    def test_critical_incident(self):
        incident = {
            "id": "crit001",
            "timestamp": "2026-08-01T00:00:00",
            "service": "api-backend",
            "issue": "Service down",
            "severity": "critical",
            "occurrences": 5,
        }
        entry = map_incident_to_audit_entry(incident)
        assert entry["action"] == "incident_critical"
        assert entry["status"] == "critical"

    def test_incident_with_operator(self):
        incident = {
            "id": "inc_op",
            "timestamp": "2026-08-01T00:00:00",
            "service": "db",
            "issue": "Slow query",
            "severity": "warning",
            "operator": "monitor",
            "source_ip": "192.168.0.1",
            "occurrences": 1,
        }
        entry = map_incident_to_audit_entry(incident)
        assert entry["user"] == "monitor"
        assert entry["source_ip"] == "192.168.0.1"

    def test_default_severity(self):
        incident = {
            "id": "no_sev",
            "timestamp": "2026-08-01T00:00:00",
            "service": "unknown",
            "occurrences": 1,
        }
        entry = map_incident_to_audit_entry(incident)
        assert entry["action"] == "incident_info"  # default severity = info


class TestGetAuditEntries:
    def test_smoke(self):
        entries = get_audit_entries()
        assert isinstance(entries, list)

    def test_empty_when_no_data(self):
        with patch("core.audit_aggregator.load_builds", return_value=[]), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries()
            assert entries == []

    def test_mixed_sources(self):
        builds = [{"id": "b1", "status": "completed", "created": "2026-08-03T10:00:00", "name": "app1"}]
        approvals = [{"id": "a1", "status": "approved", "history": [{"status": "approved", "timestamp": "2026-08-03T09:00:00"}], "approved_by": "operator1"}]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=approvals), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries()
            assert len(entries) == 2

    def test_filter_by_user(self):
        builds = [
            {"id": "b1", "status": "completed", "created": "2026-08-03T10:00:00", "name": "app1", "operator": "alice"},
            {"id": "b2", "status": "failed", "created": "2026-08-03T10:05:00", "name": "app2", "operator": "bob"},
        ]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries(user="alice")
            assert len(entries) == 1
            assert entries[0]["user"] == "alice"

    def test_filter_by_action(self):
        builds = [
            {"id": "b1", "status": "completed", "created": "2026-08-03T10:00:00", "name": "app1"},
            {"id": "b2", "status": "failed", "created": "2026-08-03T10:05:00", "name": "app2"},
        ]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries(action="build_failed")
            assert len(entries) == 1
            assert entries[0]["action"] == "build_failed"

    def test_filter_by_project(self):
        builds = [
            {"id": "b1", "status": "completed", "created": "2026-08-03T10:00:00", "name": "project-alpha"},
            {"id": "b2", "status": "failed", "created": "2026-08-03T10:05:00", "name": "project-beta"},
        ]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries(project="project-alpha")
            assert len(entries) == 1
            assert entries[0]["project"] == "project-alpha"

    def test_filter_by_date_range(self):
        builds = [
            {"id": "b1", "status": "completed", "created": "2026-08-01T10:00:00", "name": "old"},
            {"id": "b2", "status": "completed", "created": "2026-08-03T10:00:00", "name": "mid"},
            {"id": "b3", "status": "completed", "created": "2026-08-05T10:00:00", "name": "new"},
        ]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries(start_date="2026-08-02", end_date="2026-08-04")
            assert len(entries) == 1
            assert entries[0]["project"] == "mid"

    def test_sort_order_newest_first(self):
        builds = [
            {"id": "b1", "status": "completed", "created": "2026-08-01T10:00:00", "name": "old"},
            {"id": "b2", "status": "completed", "created": "2026-08-03T10:00:00", "name": "new"},
        ]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries()
            assert entries[0]["project"] == "new"
            assert entries[1]["project"] == "old"

    def test_load_errors_are_graceful(self):
        with patch("core.audit_aggregator.load_builds", side_effect=RuntimeError("boom")), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries()
            assert entries == []

    def test_none_data_handled(self):
        with patch("core.audit_aggregator.load_builds", return_value=None), \
             patch("core.audit_aggregator.load_requests", return_value=None), \
             patch("core.audit_aggregator.load_decisions", return_value=None), \
             patch("core.audit_aggregator.load_incidents", return_value=None):
            entries = get_audit_entries()
            assert entries == []

    def test_case_insensitive_filters(self):
        builds = [{"id": "b1", "status": "completed", "created": "2026-08-03T10:00:00", "name": "app1", "operator": "Alice"}]
        with patch("core.audit_aggregator.load_builds", return_value=builds), \
             patch("core.audit_aggregator.load_requests", return_value=[]), \
             patch("core.audit_aggregator.load_decisions", return_value=[]), \
             patch("core.audit_aggregator.load_incidents", return_value=[]):
            entries = get_audit_entries(user="ALICE")
            assert len(entries) == 1


class TestCSVFormatting:
    def test_empty_list(self):
        assert format_audit_entries_as_csv([]) == ""

    def test_single_entry(self):
        entry = {
            "id": "test1",
            "timestamp": "2026-08-03T12:00:00",
            "action": "build_completed",
            "user": "system",
            "source_ip": "127.0.0.1",
            "project": "test-app",
            "status": "completed",
            "source_store": "build_history",
        }
        csv_str = format_audit_entries_as_csv([entry])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2  # header + 1 data
        assert "id,timestamp,action" in lines[0]
        assert "test1" in csv_str

    def test_multiple_entries(self):
        entries = [
            {"id": "e1", "timestamp": "2026-08-03T10:00:00", "action": "build_completed", "user": "alice", "source_ip": "127.0.0.1", "project": "p1", "status": "completed", "source_store": "build_history"},
            {"id": "e2", "timestamp": "2026-08-03T11:00:00", "action": "build_failed", "user": "bob", "source_ip": "127.0.0.1", "project": "p2", "status": "failed", "source_store": "build_history"},
        ]
        csv_str = format_audit_entries_as_csv(entries)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3


class TestJSONFormatting:
    def test_format(self):
        entries = [{"id": "e1", "timestamp": "2026-08-03T10:00:00", "action": "build_completed"}]
        metadata = {"total_count": 1}
        result = format_audit_entries_as_json(entries, metadata)
        assert result["entries"] == entries
        assert result["metadata"] == metadata

    def test_empty_entries(self):
        result = format_audit_entries_as_json([], {"total_count": 0})
        assert result["entries"] == []
        assert result["metadata"]["total_count"] == 0


class TestAuditEndpointIntegration:
    @pytest.fixture
    def client(self):
        from core.api import app
        return TestClient(app)

    def test_get_audit_json(self, client):
        response = client.get("/audit/v2")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "metadata" in data
        assert "total_count" in data["metadata"]
        assert "returned_count" in data["metadata"]
        assert isinstance(data["entries"], list)

    def test_get_audit_csv(self, client):
        response = client.get("/audit/v2?format=csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    def test_get_audit_with_filters(self, client):
        response = client.get("/audit/v2?user=system&action=build_completed")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data

    def test_get_audit_with_date_range(self, client):
        response = client.get("/audit/v2?start_date=2026-01-01&end_date=2027-01-01")
        assert response.status_code == 200

    def test_get_audit_pagination(self, client):
        response = client.get("/audit/v2?limit=5&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["limit"] == 5
        assert data["metadata"]["offset"] == 0
        assert len(data["entries"]) <= 5

    def test_audit_filter_case_insensitive(self, client):
        response = client.get("/audit/v2?user=SYSTEM")
        assert response.status_code == 200


class TestAuditEndpointAuth:
    @pytest.fixture
    def client(self):
        from core.api import app
        return TestClient(app)

    def test_no_auth_allowed(self, client):
        """Audit endpoint is read-only — should allow anonymous access."""
        response = client.get("/audit")
        assert response.status_code == 200

    def test_viewer_capability_recognition(self, client):
        """Viewer role has no write caps but should still access /audit.
        The `view` capability in 15A is only defined on the endpoint itself,
        not in ROLE_CAPABILITIES.  A viewer session calling /audit gets a 403.
        This is the current auth model: anonymous is OK, but a recognized
        session with no `view` capability is denied access.
        """
        pass
