# tests/test_tailscale_discovery.py
import pytest, sys
sys.path.insert(0, "/project/ai-orchestrator")

from unittest.mock import patch


class TestTailscaleClassifier:
    def test_classify_subnet_router(self):
        from core.tailscale_discovery import _classify_node
        peer = {"AdvertiseRoutes": ["192.168.99.0/24"], "exitNode": False}
        assert _classify_node(peer) == "SUBNET_ROUTER"

    def test_classify_ordinary_client(self):
        from core.tailscale_discovery import _classify_node
        peer = {"AdvertiseRoutes": [], "exitNode": False, "Peer": "something"}
        assert _classify_node(peer) == "ORDINARY_CLIENT"

    def test_classify_exit_node(self):
        from core.tailscale_discovery import _classify_node
        peer = {"exitNode": True}
        assert _classify_node(peer) == "EXIT_NODE"

    def test_classify_direct_peer(self):
        from core.tailscale_discovery import _classify_node
        peer = {"AdvertiseRoutes": [], "exitNode": False, "Direct": True}
        assert _classify_node(peer) == "DIRECT_PEER"

    def test_classify_direct_peer_takes_precedence_over_ordinary(self):
        from core.tailscale_discovery import _classify_node
        # No routes, not exit node, but Direct=True should still be DIRECT_PEER
        peer = {"AdvertiseRoutes": [], "exitNode": False, "Direct": True}
        assert _classify_node(peer) == "DIRECT_PEER"

    def test_parse_tailscale_status_json(self):
        from core.tailscale_discovery import _parse_status_json
        sample = {
            "Self": {"HostName": "pve", "DNSName": "pve.tail-scale.ts.net.",
                     "TailnetIPs": ["100.83.4.27"], "AdvertiseRoutes": ["192.168.99.0/24"]},
            "Peer": {
                "node-b": {"HostName": "pve-b", "DNSName": "pve-b.tail-scale.ts.net.",
                           "TailnetIPs": ["100.89.97.76"], "AdvertiseRoutes": ["192.168.1.0/24"],
                           "Online": True, "Direct": True, "Latency": {"PingMs": 218.5}}
            }
        }
        peers, routes = _parse_status_json(sample)
        assert "pve-b" in peers
        assert peers["pve-b"]["role"] == "SUBNET_ROUTER"
        assert peers["pve-b"]["direct"] is True
        # More specific: check the exact subnet route for this advertiser
        pve_b_routes = [r for r in routes if r["advertiser"] == "pve-b"]
        assert len(pve_b_routes) == 1
        assert pve_b_routes[0]["subnet"] == "192.168.1.0/24"
        pve_routes = [r for r in routes if r["advertiser"] == "pve"]
        assert len(pve_routes) == 1
        assert pve_routes[0]["subnet"] == "192.168.99.0/24"


class TestDiscoverOnNode:
    @patch("core.tailscale_discovery._ssh")
    def test_discover_tailscale_on_node_success(self, mock_ssh):
        from core.tailscale_discovery import discover_tailscale_on_node
        mock_ssh.side_effect = [
            (
                '{"Self":{"HostName":"test","DNSName":"test.ts.net",'
                '"TailnetIPs":["100.83.4.27"],"AdvertiseRoutes":[]}}',
                "", 0
            ),
            ("default via 192.168.1.1 dev eth0\n", "", 0),
        ]
        node = {"name": "test-node", "host": "192.168.1.1", "ssh_user": "root", "ssh_key": "/tmp/key"}
        result = discover_tailscale_on_node(node)
        assert result["reachable"] is True
        assert result["node"] == "test-node"
        assert "test" in result["peers"]
        assert "routing_table" in result

    @patch("core.tailscale_discovery._ssh")
    def test_discover_tailscale_on_node_ssh_fails(self, mock_ssh):
        from core.tailscale_discovery import discover_tailscale_on_node
        mock_ssh.return_value = ("", "connection refused", 255)
        node = {"name": "test-node", "host": "192.168.1.1", "ssh_user": "root", "ssh_key": "/tmp/key"}
        result = discover_tailscale_on_node(node)
        assert result["reachable"] is False
        assert "connection refused" in result["error"]

    @patch("core.tailscale_discovery._ssh")
    def test_discover_tailscale_on_node_json_invalid(self, mock_ssh):
        from core.tailscale_discovery import discover_tailscale_on_node
        mock_ssh.side_effect = [
            ("not valid json", "", 0),
            ("", "", 0),
        ]
        node = {"name": "test-node", "host": "192.168.1.1", "ssh_user": "root", "ssh_key": "/tmp/key"}
        result = discover_tailscale_on_node(node)
        assert result["reachable"] is True
        assert "json parse error" in result["error"]


class TestDiscoverAllNodes:
    @patch("core.tailscale_discovery._ssh")
    def test_discover_all_nodes(self, mock_ssh):
        from core.tailscale_discovery import discover_all_nodes, TAILSCALE_NODES
        mock_ssh.side_effect = [
            ('{"Self":{"HostName":"n1","DNSName":"n1.ts.net","TailnetIPs":["100.1"]}}', "", 0),
            ("", "", 0),
            ('{"Self":{"HostName":"n2","DNSName":"n2.ts.net","TailnetIPs":["100.2"]}}', "", 0),
            ("", "", 0),
        ]
        results = discover_all_nodes()
        # All configured nodes should appear in results
        for nd in TAILSCALE_NODES:
            assert nd["name"] in results
            assert results[nd["name"]]["node"] == nd["name"]

