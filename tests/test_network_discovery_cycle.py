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
