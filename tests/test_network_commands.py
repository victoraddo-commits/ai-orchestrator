"""Tests for Task 9: Natural language network status command."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def setup_mem_dir():
    """Point memory at a temp dir so tests don't touch real state."""
    mem_dir = Path(tempfile.mkdtemp())
    old_mem = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(mem_dir)

    # Minimal graph store so load_graph() returns something sane
    import json
    graph_file = mem_dir / "network_topology.json"
    graph_file.write_text(json.dumps({
        "sites": {
            "SITE-A": {
                "name": "SITE-A",
                "lan_subnet": "192.168.99.0/24",
                "gateway": "192.168.99.254",
                "proxmox": {
                    "name": "pve",
                    "tailscale_ip": "100.83.4.27",
                    "online": True,
                },
                "lxcs": ["CT101", "CT102"],
                "vms": [],
            },
            "SITE-B": {
                "name": "SITE-B",
                "lan_subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "proxmox": {
                    "name": "pve-b",
                    "tailscale_ip": "100.89.97.76",
                    "online": True,
                },
                "lxcs": ["CT103"],
                "vms": [],
            },
        },
        "tailscale": {
            "peers": {},
            "subnet_routes": {"192.168.1.0/24": {"advertiser": "pve-b", "accepted": True}},
        },
        "tunnel": {"status": "HEALTHY", "a_to_b_latency_ms": 12, "b_to_a_latency_ms": 11},
        "last_discovery": "2026-08-30T12:00:00Z",
    }))

    yield mem_dir

    if old_mem:
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = old_mem
    else:
        os.environ.pop("AI_ORCHESTRATOR_MEMORY_DIR", None)

    import shutil
    shutil.rmtree(mem_dir, ignore_errors=True)


class TestNetworkStatusCommand:
    """Task 9: Natural language network status command dispatch."""

    @pytest.mark.parametrize("phrase", [
        "what's connected",
        "what is connected",
        "Kai, what's connected?",
        "Kai, what is connected?",
        "show network",
        "Kai, show network",
        "show network?",
        "network status",
        "Kai, network status",
        "network status?",
        "kai, network status",
    ])
    def test_dispatch_returns_network_summary(self, phrase, setup_mem_dir):
        """Every variant dispatches to get_natural_summary and returns a reply."""
        from core.kai.commands import dispatch

        # Mock get_natural_summary to avoid graph content assumptions
        mock_summary = "SITE-A and SITE-B are connected through Tailscale. SITE-A (pve) at 100.83.4.27 routes 192.168.99.0/24 (2 LXCs, 0 VMs). SITE-B (pve-b) at 100.89.97.76 routes 192.168.1.0/24 (1 LXCs, 0 VMs). Active subnet routes: 192.168.1.0/24. Site-to-site tunnel: HEALTHY. Latency A→B: 12ms, B→A: 11ms. Packet loss: 0%"

        with patch("core.kai.commands.get_natural_summary", return_value=mock_summary) as mock_get:
            result = dispatch(phrase)

        assert result["matched"] is True
        assert result["error"] is None
        assert "reply" in result["result"]
        assert mock_get.called
        assert "SITE-A" in result["result"]["reply"]
        assert "SITE-B" in result["result"]["reply"]
        assert "Tailscale" in result["result"]["reply"]

    def test_unmatched_command_returns_matched_false(self, setup_mem_dir):
        """Unknown phrases don't match."""
        from core.kai.commands import dispatch

        result = dispatch("kai, do something weird")
        assert result["matched"] is False
        assert result["error"] is not None

    def test_empty_input_returns_matched_false(self, setup_mem_dir):
        """Blank input doesn't crash."""
        from core.kai.commands import dispatch

        result = dispatch("")
        assert result["matched"] is False

    @patch("core.kai.commands.get_natural_summary")
    @patch("core.kai.commands.load_graph")
    def test_handlers_call_correct_functions(self, mock_load_graph, mock_get_natural_summary, setup_mem_dir):
        """Handler calls load_graph() then get_natural_summary(graph)."""
        from core.kai.commands import dispatch, _handle_network_status

        mock_graph = {"sites": {}, "tailscale": {}, "tunnel": {}}
        mock_load_graph.return_value = mock_graph
        mock_get_natural_summary.return_value = "Test topology summary"

        result = _handle_network_status()

        mock_load_graph.assert_called_once()
        mock_get_natural_summary.assert_called_once_with(mock_graph)
        assert result["reply"] == "Test topology summary"
