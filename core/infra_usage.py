"""Data-usage collection for the Kai Command Center infra panel.

Collects disk usage (``df``) and network counters (``/proc/net/dev``) for the
Proxmox nodes, each running LXC's rootfs, the media/backup mounts, and the
VMs, and derives byte/second rates from successive samples.

The parsing and rate math live in pure functions (``parse_*`` / ``compute_rates``
/ ``build_usage``) so they can be unit-tested with fixtures. Collection is a
single SSH call to Proxmox B which fans out to each CT in parallel and hops to
Proxmox C; results are cached and refreshed in a background thread so the API
never blocks on a slow node.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

SSH_KEY = os.environ.get("KAI_USAGE_SSH_KEY", "/root/.ssh/kai_pve_usage")
PVE_B_HOST = os.environ.get("KAI_USAGE_PVE_B", "192.168.1.110")
PVE_C_HOST = os.environ.get("KAI_USAGE_PVE_C", "100.116.165.100")
SSH_CONNECT_TIMEOUT = int(os.environ.get("KAI_USAGE_SSH_CONNECT_TIMEOUT", "6"))
COLLECT_TIMEOUT = float(os.environ.get("KAI_USAGE_TIMEOUT", "25"))
TTL = float(os.environ.get("KAI_USAGE_TTL", "8"))

_PHYSICAL_IFACE = re.compile(r"^(eth|en|nic|wl|bond|em)")
_PSEUDO_FS = {
    "tmpfs", "devtmpfs", "udev", "shm", "overlay", "proc", "sysfs",
    "cgroup", "cgroup2", "efivarfs", "none", "ramfs", "mqueue",
}
_SKIP_MOUNT_PREFIXES = ("/dev", "/run", "/sys", "/proc", "/etc/pve")


# ── pure parsing helpers ────────────────────────────────────────────────────

def _is_pseudo_fs(fs: str, mount: str) -> bool:
    if fs in _PSEUDO_FS or fs.startswith(("tmpfs", "devtmpfs", "overlay")):
        return True
    for prefix in _SKIP_MOUNT_PREFIXES:
        if mount == prefix or mount.startswith(prefix + "/"):
            return True
    return False


def parse_df(text: str) -> list[dict]:
    """Parse ``df -P -B1`` output into disk dicts (bytes)."""
    disks: list[dict] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[0] == "Filesystem":
            continue
        cap = parts[-2]
        if not cap.endswith("%"):
            continue
        mount = parts[-1]
        fs = " ".join(parts[:-5]) if len(parts) > 6 else parts[0]
        try:
            total = int(parts[-5])
            used = int(parts[-4])
            avail = int(parts[-3])
            pct = int(cap[:-1])
        except ValueError:
            continue
        if _is_pseudo_fs(fs, mount):
            continue
        disks.append({
            "filesystem": fs, "mount": mount, "total": total, "used": used,
            "avail": avail, "pct": pct,
            "kind": "network" if ":" in fs else "local",
        })
    return disks


def parse_netdev(text: str) -> dict[str, dict]:
    """Parse ``/proc/net/dev`` into ``{iface: {rx, tx, ...}}`` byte counters."""
    out: dict[str, dict] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name.startswith(("Inter", "face")):
            continue
        nums: list[int] = []
        for tok in rest.split():
            try:
                nums.append(int(tok))
            except ValueError:
                nums.append(0)
        if len(nums) < 16:
            continue
        out[name] = {
            "rx": nums[0], "tx": nums[8],
            "packets_rx": nums[1], "packets_tx": nums[9],
        }
    return out


def sum_iface_bytes(net: dict[str, dict]) -> dict[str, int]:
    """Sum rx/tx over physical interfaces only (skips lo, virtual, bridges)."""
    rx = tx = 0
    for iface, counts in (net or {}).items():
        if not _PHYSICAL_IFACE.match(iface):
            continue
        rx += int(counts.get("rx", 0))
        tx += int(counts.get("tx", 0))
    return {"rx": rx, "tx": tx}


def parse_pct(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in ("VMID",) or not parts[0].isdigit():
            continue
        rows.append({"vmid": int(parts[0]), "status": parts[1],
                     "name": parts[-1] if len(parts) > 2 else ""})
    return rows


def parse_qm(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] in ("VMID",) or not parts[0].isdigit():
            continue
        try:
            mem = int(parts[3])
            bootdisk = float(parts[4])
        except ValueError:
            continue
        rows.append({"vmid": int(parts[0]), "name": parts[1], "status": parts[2],
                     "mem_mb": mem, "bootdisk_gb": bootdisk})
    return rows


def parse_sections(text: str) -> dict[str, str]:
    """Split the collector output on top-level ``===NAME===`` markers."""
    sections: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^===([A-Z]+)===$", line.strip())
        if m:
            if cur is not None:
                sections[cur] = "\n".join(buf)
            cur = m.group(1)
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf)
    return sections


def parse_ct_blocks(text: str) -> dict[int, dict]:
    """Parse the ``###CT:<id>### ... ###END:<id>###`` per-container blocks."""
    out: dict[int, dict] = {}
    for m in re.finditer(r"###CT:(\d+)###\n(.*?)\n?###END:\1###", text or "", re.S):
        vmid = int(m.group(1))
        body = m.group(2)
        if "---NET---" in body:
            df_part, net_part = body.split("---NET---", 1)
        else:
            df_part, net_part = body, ""
        out[vmid] = {"disks": parse_df(df_part), "net": parse_netdev(net_part)}
    return out


def parse_pvec(section_text: str) -> dict:
    """Parse the Proxmox C section (markers ``DF`` then ``NET``)."""
    df_lines: list[str] = []
    net_lines: list[str] = []
    mode = None
    for line in (section_text or "").splitlines():
        s = line.strip()
        if s == "DF":
            mode = "df"
            continue
        if s == "NET":
            mode = "net"
            continue
        if mode == "df":
            df_lines.append(line)
        elif mode == "net":
            net_lines.append(line)
    return {"disks": parse_df("\n".join(df_lines)),
            "net": parse_netdev("\n".join(net_lines))}


def compute_rates(prev: dict[str, dict], cur: dict[str, dict], dt: float | None) -> dict | None:
    """Per-interface byte/second rates; ``None`` when there is no prior sample."""
    if not dt or dt <= 0:
        return None
    out: dict[str, dict] = {}
    for iface, counts in (cur or {}).items():
        before = (prev or {}).get(iface)
        if not before:
            continue
        out[iface] = {
            "rx_rate": max(0.0, (counts["rx"] - before["rx"]) / dt),
            "tx_rate": max(0.0, (counts["tx"] - before["tx"]) / dt),
        }
    return out


def _mounts(disks: list[dict]) -> list[dict]:
    return [d for d in disks
            if d["mount"].startswith("/mnt") or d["mount"].startswith("/srv")]


def _flat_net(usage: dict | None) -> dict[str, dict]:
    """Flatten a usage snapshot into ``{"host:<name>:<iface>": counts}`` keys."""
    flat: dict[str, dict] = {}
    for host in (usage or {}).get("hosts", []):
        for iface, counts in (host.get("net", {}).get("interfaces") or {}).items():
            flat[f"host:{host['name']}:{iface}"] = counts
        for ct in host.get("containers", []):
            for iface, counts in (ct.get("net", {}).get("interfaces") or {}).items():
                flat[f"ct:{ct['vmid']}:{iface}"] = counts
    return flat


def build_usage(pveb_text: str, prev: dict | None = None, now: float | None = None) -> dict:
    """Assemble the full usage snapshot from raw collector output."""
    now = time.time() if now is None else now
    sections = parse_sections(pveb_text)
    prev_flat = _flat_net(prev)
    dt = (now - prev["ts"]) if (prev and prev.get("ts")) else None

    def net_block(ifaces: dict, scope: str) -> dict:
        prev_ifaces = {k.rsplit(":", 1)[-1]: v
                       for k, v in prev_flat.items() if k.startswith(scope + ":")}
        rates = compute_rates(prev_ifaces, ifaces, dt)
        totals = sum_iface_bytes(ifaces)
        if rates is None:
            rx_rate = tx_rate = None
        else:
            rx_rate = sum(r["rx_rate"] for r in rates.values())
            tx_rate = sum(r["tx_rate"] for r in rates.values())
        return {"interfaces": ifaces, "rx_total": totals["rx"], "tx_total": totals["tx"],
                "rx_rate": rx_rate, "tx_rate": tx_rate}

    pct_by = {c["vmid"]: c for c in parse_pct(sections.get("PCT", ""))}
    ct_blocks = parse_ct_blocks(sections.get("CTSTART", ""))

    containers: list[dict] = []
    for vmid, blk in sorted(ct_blocks.items()):
        meta = pct_by.get(vmid, {})
        if meta and meta.get("status") != "running":
            continue
        disks = blk.get("disks") or []
        root = next((d for d in disks if d["mount"] == "/"), disks[0] if disks else None)
        containers.append({
            "vmid": vmid, "name": meta.get("name", ""),
            "status": meta.get("status", "running"),
            "disk": root, "net": net_block(blk.get("net") or {}, f"ct:{vmid}"),
        })

    pveb_disks = parse_df(sections.get("DF", ""))
    pveb_net = parse_netdev(sections.get("NET", ""))
    pvec = parse_pvec(sections.get("PVEC", ""))

    hosts = [
        {"name": "pve-b", "host": PVE_B_HOST, "reachable": True, "error": None,
         "disks": pveb_disks, "mounts": _mounts(pveb_disks),
         "net": net_block(pveb_net, "host:pve-b:"),
         "containers": containers,
         "vms": parse_qm(sections.get("QM", ""))},
        {"name": "pve-c", "host": PVE_C_HOST,
         "reachable": bool(pvec["disks"] or pvec["net"]),
         "error": None if (pvec["disks"] or pvec["net"]) else "unreachable",
         "disks": pvec["disks"], "mounts": _mounts(pvec["disks"]),
         "net": net_block(pvec["net"], "host:pve-c:"),
         "containers": [], "vms": []},
    ]

    mounts: list[dict] = []
    for host in hosts:
        for disk in host["mounts"]:
            mounts.append({"host": host["name"], **disk})

    disk_total = disk_used = 0
    seen: set[tuple[str, str]] = set()
    for host in hosts:
        for disk in host["disks"]:
            if disk["kind"] == "network" or disk["mount"].startswith("/var/lib/lxc"):
                continue
            key = (host["name"], disk["filesystem"])
            if key in seen:
                continue
            seen.add(key)
            disk_total += disk["total"]
            disk_used += disk["used"]
        for ct in host["containers"]:
            disk = ct.get("disk")
            if not disk:
                continue
            key = (host["name"], disk["filesystem"])
            if key in seen:
                continue
            seen.add(key)
            disk_total += disk["total"]
            disk_used += disk["used"]

    rx_total = sum(h["net"]["rx_total"] for h in hosts) + \
        sum(ct["net"]["rx_total"] for h in hosts for ct in h["containers"])
    tx_total = sum(h["net"]["tx_total"] for h in hosts) + \
        sum(ct["net"]["tx_total"] for h in hosts for ct in h["containers"])
    if dt is None:
        rx_rate = tx_rate = None
    else:
        rx_rate = sum((h["net"]["rx_rate"] or 0.0) for h in hosts) + \
            sum((ct["net"]["rx_rate"] or 0.0) for h in hosts for ct in h["containers"])
        tx_rate = sum((h["net"]["tx_rate"] or 0.0) for h in hosts) + \
            sum((ct["net"]["tx_rate"] or 0.0) for h in hosts for ct in h["containers"])

    return {
        "schema": 1,
        "ts": now,
        "interval_s": dt,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "hosts": hosts,
        "mounts": mounts,
        "totals": {
            "disk_total": disk_total, "disk_used": disk_used,
            "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0.0,
            "rx_total": rx_total, "tx_total": tx_total,
            "rx_rate": rx_rate, "tx_rate": tx_rate,
        },
    }


# ── live collection (SSH) ───────────────────────────────────────────────────

_REMOTE_SCRIPT = r"""
set -u
PVEC="$1"
echo '===DF==='
timeout 6 df -P -B1
echo '===NET==='
timeout 4 cat /proc/net/dev
echo '===PCT==='
timeout 5 pct list 2>/dev/null
echo '===QM==='
timeout 5 qm list 2>/dev/null
echo '===PVEC==='
echo DF
timeout 16 ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "root@$PVEC" 'df -P -B1; echo NET; cat /proc/net/dev' 2>&1 </dev/null || true
echo '===CTSTART==='
D=$(mktemp -d)
ids=$(timeout 5 pct list 2>/dev/null | awk 'NR>1 && $2=="running"{print $1}')
for id in $ids; do
  ( timeout 5 pct exec "$id" -- sh -c 'df -P -B1 / 2>/dev/null; echo ---NET---; cat /proc/net/dev 2>/dev/null' > "$D/$id" 2>/dev/null </dev/null ) &
done
wait
for id in $ids; do echo "###CT:$id###"; cat "$D/$id" 2>/dev/null; echo "###END:$id###"; done
rm -rf "$D"
echo '===CTEND==='
"""


def _run_remote() -> str:
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        PVE_B_HOST, "bash -s -- " + PVE_C_HOST,
    ]
    proc = subprocess.run(cmd, input=_REMOTE_SCRIPT, text=True,
                          capture_output=True, timeout=COLLECT_TIMEOUT)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError((proc.stderr or "").strip() or f"ssh exit {proc.returncode}")
    return proc.stdout


_CACHE: dict = {"data": None, "at": 0.0}
_LOCK = threading.Lock()
_REFRESHING = False


def _collect_uncached() -> dict:
    raw = _run_remote()
    with _LOCK:
        prev = _CACHE.get("data")
    return build_usage(raw, prev=prev, now=time.time())


def _refresh_worker() -> None:
    global _REFRESHING
    try:
        fresh = _collect_uncached()
        with _LOCK:
            _CACHE["data"] = fresh
            _CACHE["at"] = time.time()
    except Exception:  # noqa: BLE001
        pass
    finally:
        with _LOCK:
            _REFRESHING = False


def collect_usage(force: bool = False) -> dict:
    """Return the latest usage snapshot, refreshing in the background.

    A fresh cache (< TTL) is returned directly; a stale cache is returned
    immediately while a background thread refreshes it; the first ever call
    is synchronous (bounded by ``COLLECT_TIMEOUT``).
    """
    global _REFRESHING
    now = time.time()
    if force:
        try:
            fresh = _collect_uncached()
        except Exception as e:  # noqa: BLE001
            fresh = {"schema": 1, "ts": now, "generated_at": None,
                     "interval_s": None, "hosts": [], "mounts": [],
                     "totals": {}, "error": f"{type(e).__name__}: {e}"}
        with _LOCK:
            _CACHE["data"] = fresh
            _CACHE["at"] = time.time()
        return fresh
    with _LOCK:
        data = _CACHE.get("data")
        if data is not None and (now - _CACHE.get("at", 0.0)) < TTL:
            return data
        if data is not None:
            if not _REFRESHING:
                _REFRESHING = True
                threading.Thread(target=_refresh_worker, daemon=True).start()
            return data
    try:
        fresh = _collect_uncached()
    except Exception as e:  # noqa: BLE001
        fresh = {"schema": 1, "ts": now, "generated_at": None,
                 "interval_s": None, "hosts": [], "mounts": [],
                 "totals": {}, "error": f"{type(e).__name__}: {e}"}
    with _LOCK:
        _CACHE["data"] = fresh
        _CACHE["at"] = time.time()
    return fresh
