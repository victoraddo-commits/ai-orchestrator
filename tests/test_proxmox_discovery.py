# tests/test_proxmox_discovery.py
import pytest, sys, os
sys.path.insert(0, "/project/ai-orchestrator")


class TestProxmoxCorrelation:
    def test_correlate_tailscale_to_proxmox(self):
        from core.proxmox_discovery import _correlate_tailscale_to_node
        ts_data = {
            "pve": {
                "reachable": True,
                "peers": {
                    "pve-b": {
                        "tailscale_ip": "100.89.97.76",
                        "role": "SUBNET_ROUTER",
                        "advertise_routes": ["192.168.1.0/24"],
                    }
                }
            }
        }
        px_nodes = {
            "pve": {"name": "pve", "proxmox_ip": "192.168.99.2", "tailscale_ip": "100.83.4.27"},
            "pve-b": {"name": "pve-b", "proxmox_ip": "192.168.1.109", "tailscale_ip": "100.89.97.76"},
        }
        result = _correlate_tailscale_to_node(ts_data, px_nodes)
        assert result["pve"]["tailscale_ip"] == "100.83.4.27"
        assert result["pve-b"]["tailscale_ip"] == "100.89.97.76"


class TestProxyJumpCommand:
    """SITE-A (Proxmox A) discovery must jump through Proxmox B."""

    def test_direct_command_has_no_proxy(self):
        from core.proxmox_discovery import build_ssh_command
        cmd = build_ssh_command({"host": "192.168.1.110", "ssh_key": "/k"},
                                "ip -j addr show")
        assert not any("ProxyCommand" in a for a in cmd)
        assert cmd[-2:] == ["root@192.168.1.110", "ip -j addr show"]
        assert "BatchMode=yes" in cmd

    def test_jump_command_uses_proxycommand(self):
        from core.proxmox_discovery import build_ssh_command
        cmd = build_ssh_command(
            {"host": "100.83.4.27", "ssh_key": "/k", "proxy_jump": "192.168.1.110"},
            "hostname")
        joined = " ".join(cmd)
        assert "ProxyCommand=" in joined
        assert "-W %h:%p 192.168.1.110" in joined
        assert cmd[-2:] == ["root@100.83.4.27", "hostname"]

    def test_proxy_key_overrides_node_key(self):
        from core.proxmox_discovery import build_ssh_command
        cmd = build_ssh_command(
            {"host": "100.83.4.27", "ssh_key": "/nodekey",
             "proxy_jump": "192.168.1.110", "proxy_key": "/jumpkey"},
            "hostname")
        joined = " ".join(cmd)
        assert "-i /nodekey" in joined
        assert "-i /jumpkey" in joined


class TestDiscoverAllNodes:
    def test_site_a_jumps_through_b(self, monkeypatch):
        from core import proxmox_discovery as pd
        monkeypatch.setattr(pd, "_get_node_configs", lambda: [
            {"name": "pve", "host": "192.168.99.2"},
            {"name": "pve-b", "host": "192.168.1.109"},
        ])
        seen = {}

        def fake(node):
            seen[node["name"]] = node
            return {"node": node["name"]}

        monkeypatch.setattr(pd, "discover_node_networking", fake)
        out = pd.discover_all_nodes()

        assert seen["pve"]["host"] == "100.83.4.27"
        assert seen["pve"]["proxy_jump"] == "192.168.1.110"
        assert seen["pve-b"]["host"] == "192.168.1.110"
        assert "proxy_jump" not in seen["pve-b"]
        assert set(out) == {"pve", "pve-b"}
