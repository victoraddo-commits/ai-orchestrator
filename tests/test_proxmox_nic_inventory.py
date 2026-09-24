"""Tests for core/proxmox_discovery.py NIC inventory parsing + collection."""
from __future__ import annotations

import json

from core.proxmox_discovery import (
    _classify_iface,
    _default_ssh_key,
    collect_nic_inventory,
    parse_nic_inventory,
)


def _fixture() -> str:
    addr = [
        {"ifindex": 2, "ifname": "nic0", "flags": ["BROADCAST", "UP", "LOWER_UP"],
         "operstate": "UP", "address": "b8:2a:72:d4:98:bf",
         "altnames": ["enp1s0f0"], "master": "vmbr0",
         "addr_info": []},
        {"ifindex": 3, "ifname": "nic1", "flags": ["BROADCAST"],
         "operstate": "DOWN", "address": "b8:2a:72:d4:98:c0",
         "altnames": ["enp1s0f1"], "addr_info": []},
        {"ifindex": 7, "ifname": "vmbr0", "flags": ["BROADCAST", "UP", "LOWER_UP"],
         "operstate": "UP", "address": "b8:2a:72:d4:98:bf",
         "addr_info": [{"family": "inet", "local": "192.168.1.110"}]},
        {"ifindex": 11, "ifname": "veth100i0", "flags": ["UP"], "operstate": "UP",
         "master": "vmbr0", "address": "fe:d7:5f:34:95:35", "addr_info": []},
        {"ifindex": 6, "ifname": "tailscale0", "flags": ["UP"],
         "operstate": "UNKNOWN", "link_type": "none",
         "addr_info": [{"family": "inet", "local": "100.122.38.118"}]},
        {"ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK", "UP"],
         "operstate": "UNKNOWN", "address": "00:00:00:00:00:00", "addr_info": []},
    ]
    link = [dict(a, mtu=1500) for a in addr]
    sysfs = "\n".join([
        "lo|unknown||95154883|95154883|0|0|1",
        "nic0|up|1000|19405934402|2189144646|0|3|1",
        "nic1|down||0|0|0|0|",
        "vmbr0|up|10000|500|600|0|0|1",
        "veth100i0|up|10000|10|20|0|0|1",
        "tailscale0|unknown|-1|100|200|0|0|1",
        "bonding_masters|unknown||0|0|0|0|",
    ])
    bridge = "vmbr0"
    return (f"===ADDR===\n{json.dumps(addr)}\n===LINK===\n{json.dumps(link)}\n"
            f"===SYSFS===\n{sysfs}\n===BRIDGE===\n{bridge}\n")


def test_parse_nic_inventory_shape():
    inv = parse_nic_inventory(_fixture())
    names = {n["name"] for n in inv["nics"]}
    assert "lo" not in names
    assert "bonding_masters" not in names
    assert {"nic0", "nic1", "vmbr0", "veth100i0", "tailscale0"} <= names
    assert inv["bridges"] == ["vmbr0"]


def test_parse_nic_inventory_link_state_speed_counters():
    inv = parse_nic_inventory(_fixture())
    nic0 = next(n for n in inv["nics"] if n["name"] == "nic0")
    assert nic0["up"] is True
    assert nic0["speed_mbps"] == 1000
    assert nic0["mac"] == "b8:2a:72:d4:98:bf"
    assert nic0["master"] == "vmbr0"
    assert nic0["rx_bytes"] == 19405934402
    assert nic0["tx_errors"] == 3
    nic1 = next(n for n in inv["nics"] if n["name"] == "nic1")
    assert nic1["up"] is False
    assert nic1["speed_mbps"] is None
    assert nic1["carrier"] is False


def test_parse_nic_inventory_classifies_and_flags_wan():
    inv = parse_nic_inventory(_fixture())
    kinds = {n["name"]: n["kind"] for n in inv["nics"]}
    assert kinds["nic0"] == "physical"
    assert kinds["nic1"] == "physical"
    assert kinds["vmbr0"] == "bridge"
    assert kinds["veth100i0"] == "virtual"
    assert kinds["tailscale0"] == "vpn"
    # nic1 is physical, down, unassigned -> available for WAN; nic0 is in use.
    assert inv["available_wan"] == ["nic1"]


def test_parse_nic_inventory_ip_and_vlans():
    text = _fixture().replace(
        '"ifname": "nic0"', '"ifname": "eth0.20"')
    inv = parse_nic_inventory(text)
    vmbr0 = next(n for n in inv["nics"] if n["name"] == "vmbr0")
    assert vmbr0["ip"] == "192.168.1.110"
    assert "eth0.20" in inv["vlans"]


def test_parse_nic_inventory_tolerates_bad_json():
    inv = parse_nic_inventory("===ADDR===\nnot json\n===LINK===\n===SYSFS===\n")
    assert inv["nics"] == []


def test_classify_iface():
    assert _classify_iface("nic0", set()) == "physical"
    assert _classify_iface("vmbr0", set()) == "bridge"
    assert _classify_iface("br0", set()) == "bridge"
    assert _classify_iface("veth100i0", set()) == "virtual"
    assert _classify_iface("tailscale0", set()) == "vpn"
    assert _classify_iface("ztdiyqf2ev", set()) == "vpn"


def test_default_ssh_key_prefers_existing(tmp_path, monkeypatch):
    key = tmp_path / "k"
    key.write_text("x")
    monkeypatch.setenv("KAI_DISCOVERY_SSH_KEY", str(key))
    assert _default_ssh_key() == str(key)


def test_collect_nic_inventory_parses_ssh_output(monkeypatch):
    monkeypatch.setattr("core.proxmox_discovery._ssh",
                        lambda node, cmd: (_fixture(), "", 0))
    inv = collect_nic_inventory({"name": "pve-b", "host": "192.168.1.110"})
    assert inv["nics"]
    assert inv["available_wan"] == ["nic1"]


def test_collect_nic_inventory_reports_error(monkeypatch):
    monkeypatch.setattr("core.proxmox_discovery._ssh",
                        lambda node, cmd: ("", "connection refused", 255))
    inv = collect_nic_inventory({"name": "pve-b", "host": "192.168.1.110"})
    assert inv["nics"] == []
    assert "connection refused" in inv["error"]
