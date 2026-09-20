from datetime import datetime, timezone

from core.memory import load


# A backup is "recent" if the newest evidence of one is within this window.
# Real jobs run daily (PVE-B vzdump schedule); 48h absorbs a single missed
# night or a long-running job without turning a healthy fleet into an alert.
BACKUP_FRESHNESS_SECONDS = 48 * 60 * 60

# Interfaces that are known to be intentionally unused on PVE-B: declared
# `iface nicN inet manual` in /etc/network/interfaces, not bridged/bonded and
# not wired to any VM or container (verified 2026-09-20 via `ip -br link`,
# `bridge-ports` and pct/qm configs). Alerting on them as "inactive" only
# ever produced a recurring, un-actionable incident. Unknown inactive
# interfaces (a real NIC that dropped) are still reported.
KNOWN_UNUSED_INTERFACES = ("nic1", "nic2", "nic3")


def _now_epoch(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.timestamp()


def _task_starttime(task):
    try:
        return float(task.get("starttime") or 0)
    except (TypeError, ValueError):
        return 0.0


def _newest_backup_evidence(proxmox):
    """Return (starttime_or_ctime, status) for the strongest backup signal.

    Sources, in priority order (all are independent of the scrolling task
    window):

    - ``tasks`` — any vzdump in the (small) default recent window;
    - ``backup_tasks`` — the vzdump-filtered task query (may hold tasks the
      default window rolled past);
    - ``backup_content`` — the backup storage listing (``ctime`` is when the
      file was written; proves a backup artifact exists right now).

    Returns ``(0.0, None)`` when nothing indicates a backup at all.
    """
    candidates = []

    for task in proxmox.get("tasks", {}).get("data", []) or []:
        if task.get("type") == "vzdump":
            candidates.append((_task_starttime(task), task.get("status")))

    for task in proxmox.get("backup_tasks", {}).get("data", []) or []:
        if task.get("type") in (None, "vzdump"):
            candidates.append((_task_starttime(task), task.get("status")))

    for item in proxmox.get("backup_content", {}).get("data", []) or []:
        volid = str(item.get("volid") or item.get("path") or "")
        if "vzdump" not in volid:
            continue
        try:
            ctime = float(item.get("ctime") or 0)
        except (TypeError, ValueError):
            ctime = 0.0
        candidates.append((ctime, "OK"))

    if not candidates:
        return 0.0, None

    return max(candidates, key=lambda c: c[0])


def filter_actionable_inactive_interfaces(interfaces, known_unused=KNOWN_UNUSED_INTERFACES):
    """Inactive-but-present interface names worth reporting, sorted for a
    stable dedup key. Known-spare interfaces are excluded."""
    allowed = set(known_unused)
    return sorted(
        n.get("iface")
        for n in interfaces
        if n.get("exists") == 1
        and not n.get("active")
        and n.get("iface") not in allowed
    )


def analyze_proxmox_cluster(now=None):

    findings = []

    state = load("last_scan.json")

    proxmox = state.get("proxmox", {})

    node_entry = proxmox.get("node", {})
    if not isinstance(node_entry, dict):
        node_entry = {}
    node = node_entry.get("data", {})

    if not node:

        error = node_entry.get("error")

        if error == "auth_failed":
            # A 401/403 from Proxmox is a configuration problem, not a dead
            # node. Alerting it as "unreachable" critical (as r.json()'s
            # "Expecting value" once did) is a false-negative that also hides
            # the real fix (rotate/repair the API token).
            findings.append({
                "severity": "warning",
                "service": "proxmox-node",
                "issue": "Proxmox API authentication failed",
                "risk_score": 40,
                "recommendation": "Verify the Proxmox API token is valid and has permissions"
            })

        elif error == "invalid_json":
            findings.append({
                "severity": "critical",
                "service": "proxmox-node",
                "issue": "Proxmox node returned an unreadable (non-JSON) response",
                "risk_score": 85,
                "recommendation": "Check the Proxmox API endpoint/proxy in front of it"
            })

        else:
            findings.append({
                "severity": "critical",
                "service": "proxmox-node",
                "issue": "Proxmox node unreachable or returned no status data",
                "risk_score": 90,
                "recommendation": "Check Proxmox API connectivity and credentials"
            })

        return findings


    cpu = node.get("cpu", 0)

    memory = node.get("memory", {})

    total = memory.get("total", 1)
    used = memory.get("used", 0)

    memory_usage = used / total if total else 0


    health_score = 100


    if cpu > 0.90:
        health_score -= 25

        findings.append({
            "severity": "warning",
            "service": "proxmox-cluster",
            "issue": f"CPU pressure detected: {cpu:.2%}",
            "risk_score": 60,
            "recommendation": "Investigate high CPU workloads on the node"
        })


    if memory_usage > 0.90:
        health_score -= 25

        findings.append({
            "severity": "warning",
            "service": "proxmox-cluster",
            "issue": f"Memory pressure detected: {memory_usage:.2%}",
            "risk_score": 60,
            "recommendation": "Investigate memory usage or add capacity"
        })


    rootfs = node.get("rootfs", {})
    disk_total = rootfs.get("total", 0)
    disk_used = rootfs.get("used", 0)
    disk_usage = disk_used / disk_total if disk_total else 0

    if disk_usage > 0.90:
        health_score -= 25

        findings.append({
            "severity": "critical" if disk_usage > 0.97 else "warning",
            "service": "proxmox-cluster",
            "issue": f"Disk pressure detected: {disk_usage:.2%} of root filesystem used",
            "risk_score": 75,
            "recommendation": "Free up disk space or expand storage"
        })


    lxc = proxmox.get("lxc", {}).get("data", [])

    stopped = [
        c.get("name", c.get("vmid"))
        for c in lxc
        if c.get("status") != "running"
    ]


    if stopped:
        health_score -= len(stopped) * 10

        findings.append({
            "severity": "critical",
            "service": "proxmox-cluster",
            "issue": f"Stopped containers: {stopped}",
            "risk_score": 70,
            "recommendation": "Restart or investigate the stopped containers"
        })


    qemu = proxmox.get("qemu", {}).get("data", [])

    for vm in qemu:

        name = vm.get("name", vm.get("vmid"))

        if vm.get("status") != "running":

            health_score -= 15

            findings.append({
                "severity": "critical",
                "service": "proxmox-vm",
                "issue": f"VM stopped unexpectedly: {name}",
                "risk_score": 80,
                "recommendation": f"Investigate and restart VM {name}"
            })

            continue


        vm_cpu = vm.get("cpu", 0)

        if vm_cpu > 0.90:
            health_score -= 10

            findings.append({
                "severity": "warning",
                "service": "proxmox-vm",
                "issue": f"VM high cpu usage: {name} ({vm_cpu:.2%})",
                "risk_score": 55,
                "recommendation": f"Investigate workload on VM {name}"
            })


        vm_mem_total = vm.get("maxmem", 0)
        vm_mem_used = vm.get("mem", 0)
        vm_mem_usage = vm_mem_used / vm_mem_total if vm_mem_total else 0

        # Raw mem/maxmem ratio alone is not reliable: guests without
        # ballooning (balloon: 0) can sit at a high ratio indefinitely just
        # from normal OS disk-cache behavior (FreeBSD/OPNsense in particular).
        # PSI (pressurememorysome/full) is Proxmox's own signal for whether
        # anything is actually stalled on memory -- require it before alerting.
        vm_mem_pressure = vm.get("pressurememorysome", 0) or vm.get("pressurememoryfull", 0)

        if vm_mem_usage > 0.90 and vm_mem_pressure > 0:
            health_score -= 10

            findings.append({
                "severity": "warning",
                "service": "proxmox-vm",
                "issue": f"VM high memory usage: {name} ({vm_mem_usage:.2%})",
                "risk_score": 55,
                "recommendation": f"Investigate memory usage on VM {name}"
            })


    # Backup health is judged from the strongest *durable* evidence, not from
    # the 50-entry recent task window (which is all push_file and made this
    # check false-alarm "no backup history" 1,453 times while daily vzdump
    # jobs ran fine). See _newest_backup_evidence for the signal sources.
    backup_at, backup_status = _newest_backup_evidence(proxmox)
    backup_age = _now_epoch(now) - backup_at if backup_at else None

    node_uptime = node.get("uptime", 0)

    if backup_at == 0:
        # Nothing anywhere says a backup ever ran. A node up for <1 day gets
        # the benefit of the doubt (its first job has not come due yet).
        if node_uptime < 86400:
            pass
        else:
            findings.append({
                "severity": "warning",
                "service": "proxmox-backup",
                "issue": "No recent backup (vzdump) history found",
                "risk_score": 50,
                "recommendation": "Verify backup jobs are configured and running"
            })

    elif backup_age is not None and backup_age > BACKUP_FRESHNESS_SECONDS:
        findings.append({
            "severity": "warning",
            "service": "proxmox-backup",
            "issue": f"No recent backup (vzdump) in the last {BACKUP_FRESHNESS_SECONDS // 3600}h",
            "risk_score": 50,
            "recommendation": "Verify backup jobs are configured and running"
        })

    elif backup_status and backup_status != "OK":
        health_score -= 20

        findings.append({
            "severity": "critical",
            "service": "proxmox-backup",
            "issue": f"Most recent backup did not complete successfully: {backup_status}",
            "risk_score": 85,
            "recommendation": "Investigate and re-run the failed backup job"
        })


    network = proxmox.get("network", {}).get("data", [])

    # Sort so the dedup key is stable: the API returns interfaces in
    # nondeterministic order, so an unsorted list minted a brand-new incident
    # on every scan (6 duplicate proxmox-network incidents on 2026-09-20).
    # Known-spare interfaces are excluded -- see KNOWN_UNUSED_INTERFACES.
    down_interfaces = filter_actionable_inactive_interfaces(network)

    if down_interfaces:

        findings.append({
            "severity": "warning",
            "service": "proxmox-network",
            "issue": f"Network interfaces present but inactive: {down_interfaces}",
            "risk_score": 45,
            "recommendation": "Verify these interfaces are intentionally unused"
        })


    if health_score < 100:
        findings.append({
            "severity": "warning",
            "service": "proxmox-health-score",
            "issue": f"Health score degraded: {health_score}",
            "score": max(health_score, 0),
            "risk_score": 100 - max(health_score, 0),
            "recommendation": "Review the specific findings above"
        })
    else:
        findings.append({
            "severity": "info",
            "service": "proxmox-health-score",
            "issue": "Healthy",
            "score": 100,
            "risk_score": 0,
            "recommendation": "No action needed"
        })


    return findings


if __name__ == "__main__":
    print(analyze_proxmox_cluster())
