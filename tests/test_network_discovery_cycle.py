# tests/test_network_discovery_cycle.py
import pytest
from unittest import mock
import sys, os
sys.path.insert(0, "/project/ai-orchestrator")

class TestEmitAlert:
    def test_unknown_change_type_returns_none(self):
        from core.network_discovery_cycle import _emit_alert
        # No exception, no incident created
        _emit_alert({"type": "UNKNOWN_TYPE"})

    def test_peer_offline_calls_create_incident(self):
        from core.network_discovery_cycle import _emit_alert
        from core import incident_manager
        # Patch create_incident so we don't need writable memory
        with mock.patch.object(incident_manager, "create_incident") as mock_create:
            _emit_alert({"type": "PEER_OFFLINE", "node": "pve-b"})
            mock_create.assert_called_once_with(
                service="network",
                issue="Tailscale peer pve-b went offline",
                severity="critical",
            )


class TestInfoEventsAutoResolve:
    """Discovery/online events are informational, not problems.

    "New node discovered: X" and "peer came online" are facts about the
    network, not actionable failures -- they were being minted as OPEN
    incidents (2026-09-20: 4 stuck-open network incidents). They must be
    recorded for the audit trail but emitted so incident_manager
    auto-resolves them.
    """

    def test_node_discovered_is_emitted_as_info(self):
        from core.network_discovery_cycle import _emit_alert
        from core import incident_manager
        with mock.patch.object(incident_manager, "create_incident") as mock_create:
            _emit_alert({"type": "NODE_DISCOVERED", "node": "pve"})
            mock_create.assert_called_once_with(
                service="network",
                issue="New node discovered: pve",
                severity="info",
            )

    def test_peer_online_is_emitted_as_info(self):
        from core.network_discovery_cycle import _emit_alert
        from core import incident_manager
        with mock.patch.object(incident_manager, "create_incident") as mock_create:
            _emit_alert({"type": "PEER_ONLINE", "node": "pve"})
            mock_create.assert_called_once_with(
                service="network",
                issue="Tailscale peer pve came online",
                severity="info",
            )

    def test_route_accepted_is_emitted_as_info(self):
        from core.network_discovery_cycle import _emit_alert
        from core import incident_manager
        with mock.patch.object(incident_manager, "create_incident") as mock_create:
            _emit_alert({"type": "ROUTE_ACCEPTED", "subnet": "192.168.1.0/24"})
            mock_create.assert_called_once_with(
                service="network",
                issue="Subnet route 192.168.1.0/24 accepted",
                severity="info",
            )

    def test_route_rejection_stays_warning(self):
        from core.network_discovery_cycle import _emit_alert
        from core import incident_manager
        with mock.patch.object(incident_manager, "create_incident") as mock_create:
            _emit_alert({"type": "ROUTE_REJECTED", "subnet": "10.0.0.0/8"})
            (call,) = mock_create.call_args_list
            assert call.kwargs["severity"] == "warning"
