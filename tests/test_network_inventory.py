"""Tests for core/network_inventory.py — CC NIC inventory + app-access view."""
from __future__ import annotations

import pytest

from core import network_inventory as ni


def _graph() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-09-24T00:00:00Z",
        "last_discovery": "2026-09-24T00:00:00Z",
        "sites": {
            "SITE-B": {
                "name": "SITE-B",
                "lan_subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "services": [{"name": "orchestrator-api", "port": 8000}],
                "proxmox": {
                    "name": "pve-b", "lan_ip": "192.168.1.110",
                    "tailscale_ip": "100.122.38.118", "online": True,
                    "ssh_reachable": True,
                    "nics": [
                        {"name": "nic0", "kind": "physical", "up": True,
                         "speed_mbps": 1000, "mac": "b8:2a:72:d4:98:bf"},
                        {"name": "nic1", "kind": "physical", "up": False,
                         "speed_mbps": None, "mac": "b8:2a:72:d4:98:c0"},
                    ],
                    "bridges": ["vmbr0"], "vlans": [],
                    "available_wan": ["nic1"],
                },
            },
        },
        "tailscale": {"peers": {}, "subnet_routes": {}},
        "tunnel": {"status": "HEALTHY"},
    }


def test_collect_nic_inventory_from_graph(monkeypatch):
    monkeypatch.setattr(ni, "load_graph", _graph)
    out = ni.collect_nic_inventory()
    assert out["source"] == "topology graph"
    assert out["note"] is None
    host = out["hosts"][0]
    assert host["site"] == "SITE-B"
    assert len(host["nics"]) == 2
    assert host["available_wan"] == ["nic1"]
    assert host["ssh_reachable"] is True


def test_collect_nic_inventory_empty_notes_missing(monkeypatch):
    g = _graph()
    g["sites"]["SITE-B"]["proxmox"]["nics"] = []
    g["sites"]["SITE-B"]["proxmox"]["available_wan"] = []
    monkeypatch.setattr(ni, "load_graph", lambda: g)
    out = ni.collect_nic_inventory()
    assert out["note"] and "discovery" in out["note"]


def test_collect_nic_inventory_refresh_runs_cycle(monkeypatch):
    called = {}

    def fake_cycle():
        called["yes"] = True
        return _graph()

    monkeypatch.setattr("core.network_discovery_cycle.run_network_discovery_cycle",
                        fake_cycle)
    monkeypatch.setattr(ni, "load_graph", lambda: {})
    out = ni.collect_nic_inventory(refresh=True)
    assert called.get("yes") is True
    assert out["hosts"][0]["site"] == "SITE-B"


def test_app_access_marks_unenforced_and_opnsense_not_configured(monkeypatch):
    monkeypatch.setattr(ni, "load_graph", _graph)
    out = ni.app_access()
    assert out["services"] and out["services"][0]["site"] == "SITE-B"
    assert all(e["enforced"] is False for e in out["declared_exposures"])
    assert out["policy"]["enforced"] is False
    assert out["policy"]["opnsense"]["configured"] is False
    assert out["policy"]["wan_candidates"][0]["nic"] == "nic1"


def test_app_access_uses_passed_graph():
    out = ni.app_access(_graph())
    assert out["policy"]["wan_candidates"][0]["node"] == "pve-b"


def test_routes_registered():
    from core.cc_extra_routes import cc_extra_router
    paths = {r.path for r in cc_extra_router.routes}
    assert {"/api/network/nics", "/api/network/app-access",
            "/api/infra/usage/history"} <= paths


def test_cc_routes_gate_401_and_403(monkeypatch):
    """No creds -> 401; a valid-but-unprivileged session -> 403 (not 401)."""
    from fastapi.testclient import TestClient
    from core.api import app
    import core.authz

    client = TestClient(app)
    for path in ("/api/network/nics", "/api/network/app-access",
                 "/api/infra/usage/history"):
        assert client.get(path).status_code == 401, path

    monkeypatch.setattr(core.authz, "resolve_role", lambda tok: "viewer")
    for path in ("/api/network/nics", "/api/network/app-access",
                 "/api/infra/usage/history"):
        r = client.get(path, headers={"X-Kai-Session": "abc"})
        assert r.status_code == 403, path
