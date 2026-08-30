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


def test_build_graph_populates_sites():
    from core.topology_engine import build_graph
    ts_data = {}
    px_data = {}
    graph = build_graph(ts_data, px_data)
    assert "sites" in graph
    assert "SITE-A" in graph["sites"]
    assert "SITE-B" in graph["sites"]


def test_save_only_stamps_when_changes():
    from unittest.mock import patch
    from core.network_knowledge import _empty_graph
    with patch("core.topology_engine.detect_changes", return_value=[]):
        with patch("core.topology_engine.save_graph"):
            with patch("core.topology_engine.load_prior", return_value={"tailscale": {"peers": {}, "subnet_routes": {}}}):
                from core.topology_engine import save
                g = _empty_graph()
                g["tailscale"]["peers"] = {}
                g["tailscale"]["subnet_routes"] = {}
                save(g)
                assert "last_change" not in g or g.get("last_change") is None
