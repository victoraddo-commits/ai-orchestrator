"""Tests for Kai Notification Service.

Verifies: enqueue, dedup, ack, ack_all, unread count, filtering, stats,
and the convenience functions for findings/VPn events.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DIR = Path(tempfile.gettempdir()) / "notification_test"
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(TEST_DIR)


@pytest.fixture(autouse=True)
def setup():
    """Clean test storage before each test."""
    # Clear notifications file between tests
    notif_file = TEST_DIR / "notifications.json"
    if notif_file.exists():
        notif_file.unlink()
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if notif_file.exists():
        notif_file.unlink()


class TestEnqueue:
    """Notification creation and deduplication."""

    def test_enqueue_creates_record(self):
        """enqueue() returns a notification dict with required fields."""
        from core.notifications import NotificationManager

        notif = NotificationManager.enqueue(
            severity="critical",
            title="Test Alert",
            body="Something happened",
            source="test",
        )

        assert notif is not None
        assert notif["severity"] == "critical"
        assert notif["title"] == "Test Alert"
        assert notif["body"] == "Something happened"
        assert notif["source"] == "test"
        assert notif["acked"] is False
        assert notif["id"].startswith("notif_")
        assert "created_at" in notif

    def test_dedup_suppresses_duplicates(self):
        """Same title + source within window → None on second call."""
        from core.notifications import NotificationManager

        first = NotificationManager.enqueue(
            severity="critical",
            title="Disk Full",
            body="/dev/sda1 is 98% full",
            source="health_analyzer",
        )
        assert first is not None

        second = NotificationManager.enqueue(
            severity="critical",
            title="Disk Full",
            body="Same issue again",
            source="health_analyzer",
        )
        assert second is None  # dedup suppressed

    def test_different_title_not_deduped(self):
        """Different titles from same source are NOT suppressed."""
        from core.notifications import NotificationManager

        first = NotificationManager.enqueue(
            severity="critical",
            title="CPU High",
            body="CPU at 95%",
            source="health_analyzer",
        )
        assert first is not None

        second = NotificationManager.enqueue(
            severity="critical",
            title="Memory High",
            body="Memory at 92%",
            source="health_analyzer",
        )
        assert second is not None

    def test_invalid_severity_raises(self):
        """Unknown severity raises ValueError."""
        from core.notifications import NotificationManager

        with pytest.raises(ValueError, match="Unknown severity"):
            NotificationManager.enqueue(
                severity="super_critical",
                title="Bad",
                body="bad",
                source="test",
            )


class TestListAndFilter:
    """Listing with filters, pagination."""

    def test_list_returns_newest_first(self):
        """Notifications are returned newest-first."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "A", "body", "src")
        NotificationManager.enqueue("important", "B", "body", "src")
        NotificationManager.enqueue("informational", "C", "body", "src")

        result = NotificationManager.list_notifications()
        assert result["total"] == 3
        titles = [n["title"] for n in result["notifications"]]
        assert titles == ["C", "B", "A"]  # newest first

    def test_filter_by_severity(self):
        """Severity filter returns only matching notifications."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "Critical One", "body", "src")
        NotificationManager.enqueue("important", "Important One", "body", "src")

        result = NotificationManager.list_notifications(severity="critical")
        assert result["total"] == 1
        assert result["notifications"][0]["title"] == "Critical One"

    def test_filter_by_acked(self):
        """Acked filter returns only matching notifications."""
        from core.notifications import NotificationManager
        n = NotificationManager.enqueue("informational", "N1", "body", "src")
        NotificationManager.enqueue("informational", "N2", "body", "src")
        NotificationManager.ack(n["id"])

        unacked = NotificationManager.list_notifications(acked=False)
        assert unacked["total"] == 1
        assert unacked["notifications"][0]["title"] == "N2"

        acked_only = NotificationManager.list_notifications(acked=True)
        assert acked_only["total"] == 1
        assert acked_only["notifications"][0]["title"] == "N1"

    def test_filter_by_source(self):
        """Source filter returns only matching notifications."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "A", "body", "health_analyzer")
        NotificationManager.enqueue("critical", "B", "body", "vpn_failover")

        result = NotificationManager.list_notifications(source="vpn_failover")
        assert result["total"] == 1
        assert result["notifications"][0]["title"] == "B"

    def test_pagination(self):
        """Limit and offset work correctly."""
        from core.notifications import NotificationManager
        for i in range(5):
            NotificationManager.enqueue("informational", f"N{i}", "body", "src")

        page = NotificationManager.list_notifications(limit=2, offset=1)
        assert page["total"] == 5
        assert len(page["notifications"]) == 2


class TestAck:
    """Acknowledging notifications."""

    def test_ack_single(self):
        """ack() marks one notification as acknowledged."""
        from core.notifications import NotificationManager
        n = NotificationManager.enqueue("critical", "Alert", "body", "src")

        result = NotificationManager.ack(n["id"])
        assert result is not None
        assert result["acked"] is True
        assert result["acked_at"] is not None

    def test_ack_nonexistent(self):
        """Acking a nonexistent ID returns None."""
        from core.notifications import NotificationManager
        assert NotificationManager.ack("notif_nonexistent") is None

    def test_ack_all(self):
        """ack_all() marks all unacked notifications."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "A", "body", "src")
        NotificationManager.enqueue("important", "B", "body", "src")
        NotificationManager.enqueue("informational", "C", "body", "src")

        count = NotificationManager.ack_all()
        assert count == 3

        # Unread count should be 0
        unread = NotificationManager.unread_count()
        assert unread["total"] == 0


class TestUnreadCount:
    """Unread count by severity."""

    def test_unread_count_by_severity(self):
        """Unread count breaks down by severity."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "C1", "body", "src")
        NotificationManager.enqueue("critical", "C2", "body", "src")
        NotificationManager.enqueue("important", "I1", "body", "src")

        counts = NotificationManager.unread_count()
        assert counts["critical"] == 2
        assert counts["important"] == 1
        assert counts["informational"] == 0
        assert counts["total"] == 3

    def test_unread_count_decreases_after_ack(self):
        """Acking reduces unread count."""
        from core.notifications import NotificationManager
        n = NotificationManager.enqueue("critical", "Alert", "body", "src")
        assert NotificationManager.unread_count()["total"] == 1

        NotificationManager.ack(n["id"])
        assert NotificationManager.unread_count()["total"] == 0


class TestStats:
    """Stats aggregate."""

    def test_stats_aggregates(self):
        """get_stats returns aggregate breakdowns."""
        from core.notifications import NotificationManager
        NotificationManager.enqueue("critical", "C", "body", "health_analyzer")
        NotificationManager.enqueue("important", "I", "body", "vpn_failover")

        stats = NotificationManager.get_stats()
        assert stats["total"] == 2
        assert stats["unacked"] == 2
        assert stats["by_severity"]["critical"] == 1
        assert stats["by_severity"]["important"] == 1
        assert stats["by_source"]["health_analyzer"] == 1
        assert stats["by_source"]["vpn_failover"] == 1


class TestConvenienceFunctions:
    """Batch enqueue from findings/events."""

    def test_enqueue_from_findings(self):
        """Health findings are converted to notifications."""
        from core.notifications import enqueue_from_findings
        findings = [
            {"severity": "critical", "service": "docker", "issue": "Docker down"},
            {"severity": "warning", "service": "nginx", "issue": "High latency"},
            {"severity": "info", "service": "cron", "issue": "Last run 2h ago"},
        ]

        count = enqueue_from_findings(findings)
        assert count == 3

    def test_enqueue_from_vpn_events(self):
        """VPN events are converted to notifications."""
        from core.notifications import enqueue_from_vpn_events
        events = [
            {"severity": "critical", "type": "tunnel_down", "message": "WG to Proxmox B DOWN"},
        ]

        count = enqueue_from_vpn_events(events)
        assert count == 1


class TestModuleAndActions:
    """Notification module routing and action buttons."""

    def test_module_derived_from_source(self):
        """Module is auto-derived from source if not explicitly provided."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="critical",
            title="Health Alert",
            body="Something is broken",
            source="health_analyzer",
        )
        assert n is not None
        assert n["module"] == "health"

    def test_vpn_source_routes_to_vpn_module(self):
        """vpn_failover source → vpn module."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="critical",
            title="VPN Down",
            body="Tunnel to Proxmox B lost",
            source="vpn_failover",
        )
        assert n is not None
        assert n["module"] == "vpn"

    def test_build_failure_routes_to_build_module(self):
        """build_failure source → build module."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="important",
            title="Build Failed",
            body="Compilation error",
            source="build_failure",
        )
        assert n is not None
        assert n["module"] == "build"

    def test_unknown_source_defaults_to_system_module(self):
        """Unknown sources map to 'system' module."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="informational",
            title="Unknown Source",
            body="Test",
            source="custom_script",
        )
        assert n is not None
        assert n["module"] == "system"

    def test_explicit_module_overrides_derived(self):
        """Explicit module parameter takes precedence over source mapping."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="critical",
            title="VPN Issue",
            body="Something",
            source="vpn_failover",
            module="security",  # override
        )
        assert n is not None
        assert n["module"] == "security"

    def test_actions_included_in_record(self):
        """Notification record includes action buttons."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="critical",
            title="Alert",
            body="Body",
            source="health_analyzer",
        )
        assert n is not None
        assert isinstance(n["actions"], list)
        assert len(n["actions"]) >= 1
        # Health module should have at least VIEW and ACKNOWLEDGE
        labels = [a["label"] for a in n["actions"]]
        assert "VIEW" in labels
        assert "ACKNOWLEDGE" in labels

    def test_build_module_has_retry_action(self):
        """Build module notifications include a RETRY action."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="important",
            title="Build Stuck",
            body="Planning phase is taking too long",
            source="build_failure",
        )
        assert n is not None
        labels = [a["label"] for a in n["actions"]]
        assert "RETRY" in labels
        assert "OPEN LOG" in labels

    def test_explicit_actions_override_defaults(self):
        """Providing actions parameter uses those instead of module defaults."""
        from core.notifications import NotificationManager

        custom_actions = [
            {"label": "CUSTOM", "action": "do_thing", "target": "/custom"},
        ]
        n = NotificationManager.enqueue(
            severity="critical",
            title="Custom",
            body="Test",
            source="test",
            actions=custom_actions,
        )
        assert n is not None
        assert n["actions"] == custom_actions

    def test_default_actions_for_unknown_module(self):
        """Modules without specific actions get _DEFAULT_ACTIONS."""
        from core.notifications import NotificationManager

        n = NotificationManager.enqueue(
            severity="informational",
            title="Generic",
            body="Test",
            source="test",
            module="custom_unknown_module",
        )
        assert n is not None
        labels = [a["label"] for a in n["actions"]]
        assert "VIEW" in labels
        assert "ACKNOWLEDGE" in labels
        # Should NOT have module-specific actions like RETRY
        assert "RETRY" not in labels

    def test_enqueue_build_failure_has_build_actions(self):
        """Convenience function enqueue_build_failure() includes build actions."""
        from core.notifications import enqueue_build_failure

        n = enqueue_build_failure("TestApp", "build_42", "Out of memory")
        assert n is not None
        assert n["module"] == "build"
        labels = [a["label"] for a in n["actions"]]
        assert "RETRY" in labels
        assert "VIEW" in labels
