# tests/test_tailscale_discovery.py
import pytest, sys
sys.path.insert(0, "/project/ai-orchestrator")

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
        assert "192.168.99.0/24" in [r["subnet"] for r in routes]
