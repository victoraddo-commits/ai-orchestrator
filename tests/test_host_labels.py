"""Host-qualified CT/VM labels.

VMIDs are only unique per Proxmox host, so a bare ``CT102`` is ambiguous.
Every guest must render as ``<KIND><vmid>-<SITE>`` (``CT102-PA`` / ``CT102-PB``).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import host_labels as H  # noqa: E402


# ── site resolution ───────────────────────────────────────────────────────

def test_sites_resolve_from_names_ips_and_aliases():
    assert H.host_site("pve-a") == "PA"
    assert H.host_site("proxmox-a") == "PA"
    assert H.host_site("192.168.99.2") == "PA"
    assert H.host_site("100.83.4.27") == "PA"
    assert H.host_site("pve-b") == "PB"
    assert H.host_site("proxmox-b") == "PB"
    assert H.host_site("192.168.1.110") == "PB"
    assert H.host_site("100.122.38.118") == "PB"
    assert H.host_site("pve-c") == "PC"
    assert H.host_site("100.116.165.100") == "PC"


def test_unknown_host_is_px():
    assert H.host_site("some-host") == "PX"
    assert H.host_site(None) == "PX"
    assert H.host_site("") == "PX"


def test_first_resolvable_hint_wins():
    assert H.host_site("unknown", "pve-b") == "PB"
    assert H.host_site(None, "100.83.4.27") == "PA"


# ── labels ────────────────────────────────────────────────────────────────

def test_guest_label_host_qualified():
    assert H.guest_label("CT", 102, "pve-a") == "CT102-PA"
    assert H.guest_label("CT", 102, "pve-b") == "CT102-PB"
    assert H.guest_label("LXC", 102, "pve-c") == "CT102-PC"
    assert H.guest_label("VM", 112, "192.168.1.110") == "VM112-PB"
    assert H.guest_label("CT", 102, "unmapped") == "CT102-PX"


def test_guest_label_is_idempotent():
    assert H.guest_label("CT", "CT102-PB", "pve-a") == "CT102-PB"
    assert H.guest_label("VM", "VM112-PC", None) == "VM112-PC"


def test_label_guest_and_host_dicts():
    g = H.label_guest({"kind": "CT", "vmid": 100,
                       "name": "kai-legal-brain", "host": "pve-b"})
    assert g["label"] == "CT100-PB" and g["site"] == "PB"
    h = H.label_host({"name": "pve-c", "host": "100.116.165.100"})
    assert h["site"] == "PC" and h["site_label"] == "Proxmox C"


# ── integration: infra_usage applies the labels ───────────────────────────

def test_infra_usage_labels_containers_and_host():
    from core.infra_usage import build_usage

    raw = (
        "===PCT===\n100 running kai-legal-brain\n"
        "===CTSTART===\n###CT:100###\n"
        "---NET---\neth0: 1 0 0 0 0 0 0 0 2 0 0 0 0 0 0 0\n"
        "###END:100###\n"
    )
    usage = build_usage(raw, now=1.0)
    host = usage["hosts"][0]
    assert host["site"] == "PB" and host["site_label"] == "Proxmox B"
    assert host["containers"][0]["label"] == "CT100-PB"
