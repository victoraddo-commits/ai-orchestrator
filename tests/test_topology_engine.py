import pytest, sys, os, tempfile, shutil
sys.path.insert(0, "/project/ai-orchestrator")

class TestChangeDetection:
    def test_detect_peer_offline(self):
        from core.topology_engine import detect_changes
        prior = {"tailscale": {"peers": {"pve-b": {"online": True}}}}
        current = {"tailscale": {"peers": {"pve-b": {"online": False}}}}
        changes = detect_changes(prior, current)
        assert any(c["type"] == "PEER_OFFLINE" for c in changes)

    def test_detect_route_withdrawn(self):
        from core.topology_engine import detect_changes
        prior = {"tailscale": {"subnet_routes": {"192.168.99.0/24": {"advertiser": "pve"}}}}
        current = {"tailscale": {"subnet_routes": {}}}
        changes = detect_changes(prior, current)
        assert any(c["type"] == "ROUTE_WITHDRAWN" for c in changes)

    def test_natural_summary_generates(self):
        from core.topology_engine import get_natural_summary
        graph = {
            "sites": {
                "SITE-A": {"name": "Site A", "lxcs": [{"vmid": 100}], "vms": []},
                "SITE-B": {"name": "Site B", "lxcs": [{"vmid": 200}], "vms": []},
            },
            "tailscale": {"peers": {}},
            "tunnel": {"status": "HEALTHY"},
        }
        summary = get_natural_summary(graph)
        assert "Site A" in summary
        assert "Site B" in summary
