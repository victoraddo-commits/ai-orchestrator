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
