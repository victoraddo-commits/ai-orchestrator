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


def test_site_b_tailnet_ip_reconciled():
    from core.topology_engine import build_graph
    graph = build_graph({}, {})
    assert graph["sites"]["SITE-B"]["proxmox"]["tailscale_ip"] == "100.122.38.118"


def test_site_online_from_tailnet_peer_only():
    """A site whose node is SSH-unreachable is still online if its peer is up.

    Peers are flattened across all discovered nodes because only the reachable
    node (Proxmox B) sees the whole tailnet.
    """
    from core.topology_engine import build_graph
    ts_data = {"pve-b": {"reachable": True, "peers": {
        "pve": {"hostname": "pve", "tailscale_ip": "100.122.38.118",
                "online": True, "role": "SUBNET_ROUTER"},
        "pve [100.83.4.27]": {"hostname": "pve", "tailscale_ip": "100.83.4.27",
                              "online": True, "role": "SUBNET_ROUTER"},
    }}}
    graph = build_graph(ts_data, {})
    assert graph["sites"]["SITE-B"]["proxmox"]["online"] is True
    assert graph["sites"]["SITE-A"]["proxmox"]["online"] is True
    assert graph["sites"]["SITE-A"]["tailscale_peer"]["tailscale_ip"] == "100.83.4.27"


def test_build_sites_carries_nic_inventory():
    from core.topology_engine import build_graph
    px_data = {"pve-b": {
        "reachable": True, "lan_ip": "192.168.1.110", "gateway": "192.168.1.1",
        "nics": [{"name": "nic0", "kind": "physical", "up": True}],
        "bridges": ["vmbr0"], "vlans": [], "available_wan": ["nic1"],
    }}
    graph = build_graph({}, px_data)
    px = graph["sites"]["SITE-B"]["proxmox"]
    assert px["lan_ip"] == "192.168.1.110"
    assert px["nics"][0]["name"] == "nic0"
    assert px["bridges"] == ["vmbr0"]
    assert px["available_wan"] == ["nic1"]


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


class TestSitesConsistent:
    def test_allows_bridge_and_slave_sharing_mac(self):
        from core.topology_engine import sites_consistent
        graph = {"sites": {
            "SITE-A": {"proxmox": {"lan_ip": "192.168.1.2", "nics": [
                {"name": "nic0", "mac": "aa:bb:cc:dd:ee:01"},
                {"name": "vmbr0", "mac": "aa:bb:cc:dd:ee:01"}]}},
            "SITE-B": {"proxmox": {"lan_ip": "192.168.1.110", "nics": [
                {"name": "nic0", "mac": "aa:bb:cc:dd:ee:02"}]}},
        }}
        ok, reason = sites_consistent(graph)
        assert ok, reason

    def test_flags_cross_site_mac_collision(self):
        from core.topology_engine import sites_consistent
        graph = {"sites": {
            "SITE-A": {"proxmox": {"lan_ip": "192.168.1.2", "nics": [
                {"name": "nic0", "mac": "aa:bb:cc:dd:ee:01"}]}},
            "SITE-B": {"proxmox": {"lan_ip": "192.168.1.110", "nics": [
                {"name": "nic0", "mac": "aa:bb:cc:dd:ee:01"}]}},
        }}
        ok, reason = sites_consistent(graph)
        assert not ok and "MAC" in reason

    def test_flags_cross_site_lan_ip_collision(self):
        from core.topology_engine import sites_consistent
        graph = {"sites": {
            "SITE-A": {"proxmox": {"lan_ip": "192.168.1.2", "nics": []}},
            "SITE-B": {"proxmox": {"lan_ip": "192.168.1.2", "nics": []}},
        }}
        ok, reason = sites_consistent(graph)
        assert not ok and "LAN IP" in reason
