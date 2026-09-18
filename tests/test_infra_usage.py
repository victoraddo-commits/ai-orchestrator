"""Tests for the Command Center data-usage collector (core/infra_usage.py).

Covers the pure parsing / rate-math helpers with fixtures captured from the
live Proxmox B + Proxmox C output, plus the route registration.
"""
from __future__ import annotations

from core.infra_usage import (
    build_usage,
    compute_rates,
    parse_ct_blocks,
    parse_df,
    parse_netdev,
    parse_pct,
    parse_pvec,
    parse_qm,
    parse_sections,
    sum_iface_bytes,
)

PVEB = """\
===DF===
Filesystem                             1-blocks         Used      Available Capacity Mounted on
/dev/mapper/pve-root                41324888064  28332425216    10860290048      73% /
tmpfs                               33705869312   104570880    33601298432       1% /tmp
/dev/sdc                           244996427776 140376428544    92100255744      61% /mnt/evo
/dev/sdb1                          491106508800  12146675712   453937717248       3% /mnt/wd
100.116.165.100:/srv/kai-backups 11904379518976  41299214336 11263056805888       1% /mnt/kai-c
===NET===
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 95154883  324503    0    0    0     0          0         0 95154883  324503    0    0    0     0       0          0
  nic0: 19405934402 14941107    0    0    0     0          0         0 2189144646 9945883    0    0    0     0       0          0
tailscale0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0
 vmbr0: 500 5 0 0 0 0 0 0 600 6 0 0 0 0 0 0
===PCT===
VMID       Status     Lock         Name
100        running                 kai-legal-brain
104        stopped                 not-a-ct
===QM===
      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID
       104 kai-gpu-benchmark    running    32768            220.00 26133
===PVEC===
DF
Filesystem                 1-blocks        Used      Available Capacity Mounted on
/dev/mapper/pve-root   100861726720  4639334400    91051655168       5% /
/dev/sdb1            11904378875904 41299156992 11263056064512       1% /srv/kai-backups
NET
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  397201 2584 0 0 0 0 0 0 397201 2584 0 0 0 0 0 0
  nic0: 19862410418 15007241 0 0 0 0 0 0 125825 896778966 6412564 0 0 0 0 0 0
tailscale0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0
===CTSTART===
###CT:100###
Filesystem                          1-blocks      Used   Available Capacity Mounted on
/dev/mapper/pve-vm--100--disk--0 126227120128 1063985152 118703906816       1% /
---NET---
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0
  eth0: 3000 10 0 0 0 0 0 0 400 4 0 0 0 0 0 0
###END:100###
===CTEND===
"""


def _host(usage, name):
    return next(h for h in usage["hosts"] if h["name"] == name)


def test_parse_df_keeps_real_filesystems_and_skips_pseudo():
    disks = parse_df(
        "Filesystem 1-blocks Used Available Capacity Mounted on\n"
        "/dev/mapper/pve-root 100 40 60 40% /\n"
        "tmpfs 50 1 49 2% /run\n"
        "10.0.0.1:/backups 200 10 190 5% /mnt/kai-c\n"
    )
    mounts = {d["mount"] for d in disks}
    assert "/" in mounts and "/mnt/kai-c" in mounts
    assert "/run" not in mounts
    root = next(d for d in disks if d["mount"] == "/")
    assert (root["total"], root["used"], root["pct"]) == (100, 40, 40)
    assert next(d for d in disks if d["mount"] == "/mnt/kai-c")["kind"] == "network"


def test_parse_netdev_extracts_rx_tx_bytes():
    net = parse_netdev(
        "Inter-|   Receive  |  Transmit\n"
        " face |bytes packets errs\n"
        "    lo: 10 1 0 0 0 0 0 0 20 2 0 0 0 0 0 0\n"
        "  eth0: 300 3 0 0 0 0 0 0 400 4 0 0 0 0 0 0\n"
    )
    assert net["eth0"]["rx"] == 300
    assert net["eth0"]["tx"] == 400
    assert net["lo"]["rx"] == 10


def test_sum_iface_bytes_ignores_virtual_and_loopback():
    net = {
        "lo": {"rx": 10, "tx": 20},
        "nic0": {"rx": 100, "tx": 200},
        "tailscale0": {"rx": 1000, "tx": 2000},
        "vmbr0": {"rx": 1000, "tx": 2000},
    }
    totals = sum_iface_bytes(net)
    assert totals == {"rx": 100, "tx": 200}


def test_parse_pct_and_qm():
    cts = parse_pct("VMID Status Lock Name\n100 running kai-legal-brain\n104 stopped x\n")
    assert cts[0] == {"vmid": 100, "status": "running", "name": "kai-legal-brain"}
    vms = parse_qm(" VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n 104 kai-gpu-benchmark running 32768 220.00 1\n")
    assert vms[0]["vmid"] == 104
    assert vms[0]["bootdisk_gb"] == 220.0


def test_parse_sections_and_pvec_and_ct_blocks():
    sections = parse_sections(PVEB)
    assert "DF" in sections and "PVEC" in sections and "CTSTART" in sections
    pvec = parse_pvec(sections["PVEC"])
    assert any(d["mount"] == "/srv/kai-backups" for d in pvec["disks"])
    assert pvec["net"]["nic0"]["rx"] == 19862410418
    cts = parse_ct_blocks(sections["CTSTART"])
    assert cts[100]["net"]["eth0"]["rx"] == 3000
    assert cts[100]["disks"][0]["mount"] == "/"


def test_build_usage_assembles_hosts_containers_mounts_totals():
    usage = build_usage(PVEB, prev=None, now=1000.0)
    b = _host(usage, "pve-b")
    c = _host(usage, "pve-c")
    assert b["reachable"] and c["reachable"]

    # host network: physical nic only (not lo/tailscale/vmbr)
    assert b["net"]["rx_total"] == 19405934402
    assert b["net"]["tx_total"] == 2189144646
    assert b["net"]["rx_rate"] is None  # no previous sample yet

    # containers merged with pct names, only the running one
    assert [x["vmid"] for x in b["containers"]] == [100]
    ct = b["containers"][0]
    assert ct["name"] == "kai-legal-brain"
    assert ct["disk"]["used"] == 1063985152
    assert ct["net"]["rx_total"] == 3000

    # VMs carry their allocated boot disk
    assert b["vms"][0]["vmid"] == 104 and b["vms"][0]["bootdisk_gb"] == 220.0

    # mounts surfaced across both hosts, labelled by host
    mount_names = {(m["host"], m["mount"]) for m in usage["mounts"]}
    assert ("pve-b", "/mnt/evo") in mount_names
    assert ("pve-b", "/mnt/wd") in mount_names
    assert ("pve-c", "/srv/kai-backups") in mount_names

    # totals: local block storage only; NFS mount is counted once (at the server)
    expected_total = (
        41324888064 + 244996427776 + 491106508800 + 126227120128
        + 100861726720 + 11904378875904
    )
    expected_used = (
        28332425216 + 140376428544 + 12146675712 + 1063985152
        + 4639334400 + 41299156992
    )
    assert usage["totals"]["disk_total"] == expected_total
    assert usage["totals"]["disk_used"] == expected_used
    assert usage["totals"]["rx_total"] == 19405934402 + 19862410418 + 3000


def test_build_usage_computes_rates_from_previous_sample():
    first = build_usage(PVEB, prev=None, now=1000.0)
    doubled = PVEB.replace("  nic0: 19405934402", "  nic0: 19405934402").replace(
        "  eth0: 3000 10", "  eth0: 8000 10")
    second = build_usage(doubled, prev=first, now=1010.0)
    assert second["interval_s"] == 10.0
    b = _host(second, "pve-b")
    # nic0 unchanged -> 0 B/s; CT eth0 +5000 over 10s -> 500 B/s
    assert b["net"]["rx_rate"] == 0.0
    ct = next(x for x in b["containers"] if x["vmid"] == 100)
    assert ct["net"]["rx_rate"] == 500.0


def test_compute_rates_handles_missing_and_zero_dt():
    assert compute_rates({}, {"eth0": {"rx": 1, "tx": 1}}, 0) is None
    out = compute_rates({"eth0": {"rx": 100, "tx": 200}},
                        {"eth0": {"rx": 400, "tx": 600}}, 10)
    assert out["eth0"] == {"rx_rate": 30.0, "tx_rate": 40.0}


def test_route_registered():
    from core.cc_extra_routes import cc_extra_router

    paths = {r.path for r in cc_extra_router.routes}
    assert "/api/infra/usage" in paths
