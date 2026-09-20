"""Tests for the Proxmox health analyzer.

Regression coverage for two 2026-09-20 false positives:

1. ``proxmox-backup``: the analyzer only looked at the node's *recent task
   window* (limit=50, dominated by ``push_file``) for a ``vzdump`` task. Real
   backups run every day, but the vzdump tasks roll out of a 50-entry window,
   so the check false-alarmed "No recent backup (vzdump) history found"
   (1,453 occurrences). The analyzer must instead treat *any* of the strong,
   independent signals as evidence a backup happened:

   - a ``vzdump`` task in the (possibly small) recent window,
   - a ``vzdump`` task in a larger, explicitly-fetched vzdump-filtered list
     (``proxmox["backup_tasks"]`` -- the API supports ``?typefilter=vzdump``),
   - a recent ``vzdump-*`` file on the backup storage
     (``proxmox["backup_content"]`` -- ``storage kai-c`` -> ``/mnt/kai-c/dump``).

   Only genuinely-no-recent-backup (nothing within the window, or the newest
   signal older than the freshness threshold) is a warning.

2. ``proxmox-network``: ``nic1/nic2/nic3`` on PVE-B are declared
   ``iface nicN inet manual`` in ``/etc/network/interfaces`` with no bridge,
   bond or VM/CT wiring -- they are intentional spare/disabled NICs. Alerting
   on them as "inactive" minted a recurring open incident. Known-unused
   interfaces (allowlist) must not be flagged; unknown inactive ones still
   must, and the allowed ones must not mask a genuinely-failing interface.
"""

from datetime import datetime, timedelta, timezone

from core.memory import save
from core.proxmox_health import (
    KNOWN_UNUSED_INTERFACES,
    filter_actionable_inactive_interfaces,
    analyze_proxmox_cluster,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _ts(day, hour=0, minute=0):
    return int(datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc).timestamp())


def base_scan(**overrides):
    scan = {
        "proxmox": {
            "node": {"data": {
                "cpu": 0.10,
                "memory": {"total": 100, "used": 10},
                "rootfs": {"total": 100, "used": 10, "avail": 90},
                "uptime": 200000
            }},
            "lxc": {"data": []},
            "qemu": {"data": []},
            "tasks": {"data": []},
            "network": {"data": []}
        }
    }
    scan["proxmox"].update(overrides)
    return scan


def find(findings, service):
    return [f for f in findings if f["service"] == service]


# ---------------------------------------------------------------------------
# proxmox-backup
# ---------------------------------------------------------------------------


def test_backup_task_in_recent_window_is_not_flagged():
    save("last_scan.json", base_scan(tasks={"data": [
        {"type": "vzdump", "status": "OK", "starttime": _ts(19, 22)}
    ]}))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-backup") == []


def test_no_vzdump_task_but_recent_dump_file_is_healthy():
    # The exact production shape: recent task window is all push_file, but a
    # vzdump-filtered query (or the backup-storage listing) proves the backup
    # ran yesterday. Must NOT warn.
    save("last_scan.json", base_scan(
        tasks={"data": [
            {"type": "push_file", "status": "OK", "starttime": _ts(20, 10)}
            for _ in range(50)
        ]},
        backup_tasks={"data": [
            {"type": "vzdump", "status": "OK", "starttime": _ts(19, 12)}
        ]},
    ))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-backup") == []


def test_no_vzdump_task_but_recent_content_entry_is_healthy():
    # Fallback signal: listing the backup storage's ``content`` yields recent
    # vzdump-* files even when the task list doesn't.
    save("last_scan.json", base_scan(
        tasks={"data": [
            {"type": "push_file", "status": "OK", "starttime": _ts(20, 10)}
        ]},
        backup_content={"data": [
            {"volid": "kai-c:backup/vzdump-lxc-111-2026_09_19-05_36_57.tar.zst",
             "ctime": _ts(19, 5)},
        ]},
    ))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-backup") == []


def test_genuinely_no_backups_warns():
    save("last_scan.json", base_scan(tasks={"data": [
        {"type": "push_file", "status": "OK", "starttime": _ts(20, 10)}
    ]}))

    backup = find(analyze_proxmox_cluster(now=NOW), "proxmox-backup")
    assert len(backup) == 1
    assert backup[0]["severity"] == "warning"


def test_stale_only_backup_evidence_warns():
    # A vzdump exists but is far older than the freshness threshold (48h):
    # "there was a backup once" is not "backups are running". Warn.
    save("last_scan.json", base_scan(backup_tasks={"data": [
        {"type": "vzdump", "status": "OK", "starttime": _ts(10)}
    ]}))

    backup = find(analyze_proxmox_cluster(now=NOW), "proxmox-backup")
    assert len(backup) == 1
    assert backup[0]["severity"] == "warning"


def test_recent_failed_vzdump_is_critical():
    save("last_scan.json", base_scan(backup_tasks={"data": [
        {"type": "vzdump", "status": "job errors", "starttime": _ts(19, 12)}
    ]}))

    backup = find(analyze_proxmox_cluster(now=NOW), "proxmox-backup")
    assert len(backup) == 1
    assert backup[0]["severity"] == "critical"


def test_failed_vzdump_superseded_by_success_is_not_flagged():
    # The most recent evidence is a successful backup: the older failure is
    # history, not a current problem.
    save("last_scan.json", base_scan(backup_tasks={"data": [
        {"type": "vzdump", "status": "job errors", "starttime": _ts(18, 4)},
        {"type": "vzdump", "status": "OK", "starttime": _ts(19, 12)},
    ]}))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-backup") == []


def test_recently_booted_node_with_no_backups_is_not_flagged():
    save("last_scan.json", base_scan(
        node={"data": {
            "cpu": 0.1,
            "memory": {"total": 100, "used": 10},
            "rootfs": {"total": 100, "used": 10, "avail": 90},
            "uptime": 3600,
        }},
        tasks={"data": [{"type": "push_file", "status": "OK", "starttime": _ts(20, 10)}]},
    ))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-backup") == []


# ---------------------------------------------------------------------------
# proxmox-network
# ---------------------------------------------------------------------------


def test_known_unused_interfaces_are_not_flagged():
    save("last_scan.json", base_scan(network={"data": [
        {"iface": "nic1", "exists": 1, "active": 0},
        {"iface": "nic2", "exists": 1, "active": 0},
        {"iface": "nic3", "exists": 1, "active": 0},
    ]}))

    assert find(analyze_proxmox_cluster(now=NOW), "proxmox-network") == []


def test_unknown_inactive_interface_is_still_flagged():
    save("last_scan.json", base_scan(network={"data": [
        {"iface": "nic0", "exists": 1, "active": 0},
    ]}))

    net = find(analyze_proxmox_cluster(now=NOW), "proxmox-network")
    assert len(net) == 1
    assert "nic0" in net[0]["issue"]


def test_allowlisted_interfaces_do_not_mask_a_real_failure():
    save("last_scan.json", base_scan(network={"data": [
        {"iface": "nic1", "exists": 1, "active": 0},
        {"iface": "nic2", "exists": 1, "active": 0},
        {"iface": "nic3", "exists": 1, "active": 0},
        {"iface": "enp5s0", "exists": 1, "active": 0},
    ]}))

    net = find(analyze_proxmox_cluster(now=NOW), "proxmox-network")
    assert len(net) == 1
    assert "enp5s0" in net[0]["issue"]
    assert "nic1" not in net[0]["issue"]


def test_filter_helper_excludes_allowlist_and_sorts():
    interfaces = [
        {"iface": "nic3", "exists": 1, "active": 0},
        {"iface": "nic1", "exists": 1, "active": 0},
        {"iface": "eno2", "exists": 1, "active": 0},
        {"iface": "nic0", "exists": 1, "active": 1},
        {"iface": "nic2", "exists": 0, "active": 0},
    ]

    assert filter_actionable_inactive_interfaces(interfaces) == ["eno2"]


def test_allowlist_is_a_set_of_known_spare_pve_b_nics():
    assert {"nic1", "nic2", "nic3"} <= set(KNOWN_UNUSED_INTERFACES)
