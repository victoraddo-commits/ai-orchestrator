"""Batch CC fixes — site identity, executive classification, health gating.

Covers the regressions fixed in the 2026-09-25 CC batch:
* SITE-A must never inherit SITE-B's LAN/NICs when Proxmox A is unreachable.
* Executive must not report a transient `unknown` node as critical.
* `/health` must not report Docker as a *critical* degradation on a host that
  does not run Docker (false-degraded).
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/project/ai-orchestrator")


# ---------------------------------------------------------------------------
# Bug 8 — Site A / Site B must be distinct
# ---------------------------------------------------------------------------

def test_unreachable_site_a_does_not_inherit_site_b_lan_or_nics():
    """Proxmox A (`pve`) is SSH-unreachable; Site A's LAN/NICs must stay empty,
    never borrow Site B's (`pve-b`) data."""
    from core.topology_engine import build_graph

    px_data = {
        "pve": {"reachable": False, "lan_ip": None, "nics": [],
                "bridges": [], "vlans": [], "available_wan": []},
        "pve-b": {
            "reachable": True, "lan_ip": "192.168.1.110", "gateway": "192.168.1.1",
            "nics": [{"name": "nic0", "kind": "physical", "up": True}],
            "bridges": ["vmbr0"], "vlans": [], "available_wan": ["nic1"],
        },
    }
    graph = build_graph({}, px_data)
    a = graph["sites"]["SITE-A"]["proxmox"]
    b = graph["sites"]["SITE-B"]["proxmox"]

    assert a["name"] == "pve"
    assert b["name"] == "pve-b"
    # Site A is unreachable → honest empty inventory, its own identity.
    assert a["ssh_reachable"] is False
    assert a["lan_ip"] in (None, "")
    assert a["nics"] == []
    assert a["available_wan"] == []
    # Site B keeps its NICs.
    assert b["lan_ip"] == "192.168.1.110"
    assert b["nics"][0]["name"] == "nic0"


def test_two_sites_never_share_lan_ip_or_nic_macs():
    """Defence-in-depth: distinct sites must not present identical LAN/NICs."""
    from core.topology_engine import build_graph

    px_data = {
        "pve": {"reachable": False, "lan_ip": None, "nics": []},
        "pve-b": {
            "reachable": True, "lan_ip": "192.168.1.110",
            "nics": [{"name": "nic0", "mac": "b8:2a:72:d4:98:bf"}],
        },
    }
    graph = build_graph({}, px_data)
    a, b = graph["sites"]["SITE-A"]["proxmox"], graph["sites"]["SITE-B"]["proxmox"]
    assert a["lan_ip"] != b["lan_ip"] or not a["lan_ip"]
    assert a["nics"] != b["nics"]


def test_sites_consistent_flags_shared_lan_and_macs():
    from core.topology_engine import sites_consistent

    bad = {"sites": {
        "SITE-A": {"proxmox": {"lan_ip": "192.168.1.110", "nics": [
            {"name": "nic0", "mac": "b8:2a:72:d4:98:bf"}]}},
        "SITE-B": {"proxmox": {"lan_ip": "192.168.1.110", "nics": [
            {"name": "nic0", "mac": "b8:2a:72:d4:98:bf"}]}},
    }}
    ok, reason = sites_consistent(bad)
    assert ok is False and reason

    good = {"sites": {
        "SITE-A": {"proxmox": {"lan_ip": "", "nics": []}},
        "SITE-B": {"proxmox": {"lan_ip": "192.168.1.110", "nics": [
            {"name": "nic0", "mac": "b8:2a:72:d4:98:bf"}]}},
    }}
    ok, _ = sites_consistent(good)
    assert ok is True


def test_save_refuses_inconsistent_topology(monkeypatch):
    import pytest
    from core.topology_engine import save
    from core.network_knowledge import _empty_graph

    bad = _empty_graph()
    bad["sites"] = {
        "SITE-A": {"proxmox": {"lan_ip": "192.168.1.110",
                               "nics": [{"name": "nic0", "mac": "aa:bb"}]}},
        "SITE-B": {"proxmox": {"lan_ip": "192.168.1.110",
                               "nics": [{"name": "nic0", "mac": "aa:bb"}]}},
    }
    with pytest.raises(ValueError):
        save(bad)


# ---------------------------------------------------------------------------
# Bug 10 — executive severity classification
# ---------------------------------------------------------------------------

def test_executive_unknown_node_is_watch_not_critical(monkeypatch, tmp_path):
    from core import kai_executive as ex

    snap = {"entities": {
        "host:pve": {"type": "proxmox_node", "label": "pve", "status": "unknown"},
        "host:pve-b": {"type": "proxmox_node", "label": "pve-b", "status": "online"},
    }}
    monkeypatch.setattr(ex, "_MEMORY_DIR", tmp_path)
    import json as _json
    (tmp_path / "world_model.json").write_text(_json.dumps(snap))
    out = ex.prioritize()
    critical_labels = [c.get("entity") for c in out["critical"]]
    assert "host:pve" not in critical_labels
    assert any(w.get("entity") == "host:pve" for w in out["watch"])


def test_executive_unreachable_node_is_critical(monkeypatch, tmp_path):
    from core import kai_executive as ex
    import json as _json

    snap = {"entities": {
        "host:pve": {"type": "proxmox_node", "label": "pve",
                     "status": "unreachable"},
    }}
    monkeypatch.setattr(ex, "_MEMORY_DIR", tmp_path)
    (tmp_path / "world_model.json").write_text(_json.dumps(snap))
    out = ex.prioritize()
    assert any(c.get("entity") == "host:pve" for c in out["critical"])


# ---------------------------------------------------------------------------
# Bug 6 — health must not false-flag docker on a non-docker host
# ---------------------------------------------------------------------------

def test_health_no_critical_when_docker_not_expected(monkeypatch, tmp_path):
    """With no docker socket and no docker expectation, `/health` must not
    report a critical docker degradation."""
    from core import health as h

    monkeypatch.setattr(h, "load", lambda name: {"docker": {"available": False}})
    monkeypatch.setattr(h, "_docker_expected", lambda: False, raising=False)
    findings = h.analyze()
    docker_critical = [f for f in findings
                       if f.get("service") == "docker"
                       and f.get("severity") == "critical"]
    assert docker_critical == []
