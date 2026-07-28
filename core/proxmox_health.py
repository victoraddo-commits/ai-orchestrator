from core.memory import load


def analyze_proxmox_cluster():

    findings = []

    state = load("last_scan.json")

    proxmox = state.get("proxmox", {})

    node = proxmox.get("node", {}).get("data", {})

    if not node:

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


    tasks = proxmox.get("tasks", {}).get("data", [])

    backup_tasks = sorted(
        (t for t in tasks if t.get("type") == "vzdump"),
        key=lambda t: t.get("starttime", 0),
        reverse=True
    )

    if not backup_tasks:

        findings.append({
            "severity": "warning",
            "service": "proxmox-backup",
            "issue": "No recent backup (vzdump) history found",
            "risk_score": 50,
            "recommendation": "Verify backup jobs are configured and running"
        })

    elif backup_tasks[0].get("status") != "OK":

        health_score -= 20

        findings.append({
            "severity": "critical",
            "service": "proxmox-backup",
            "issue": f"Most recent backup did not complete successfully: {backup_tasks[0].get('status')}",
            "risk_score": 85,
            "recommendation": "Investigate and re-run the failed backup job"
        })


    network = proxmox.get("network", {}).get("data", [])

    down_interfaces = [
        n.get("iface")
        for n in network
        if n.get("exists") == 1 and not n.get("active")
    ]

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
