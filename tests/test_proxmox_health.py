from core.memory import save
from core.proxmox_health import analyze_proxmox_cluster


def base_scan(**overrides):
    scan = {
        "proxmox": {
            "node": {"data": {
                "cpu": 0.10,
                "memory": {"total": 100, "used": 10},
                "rootfs": {"total": 100, "used": 10, "avail": 90}
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


def test_node_unreachable_is_critical():
    save("last_scan.json", {"proxmox": {"node": {"data": {}}}})

    findings = analyze_proxmox_cluster()

    unreachable = find(findings, "proxmox-node")
    assert len(unreachable) == 1
    assert unreachable[0]["severity"] == "critical"
    assert "risk_score" in unreachable[0]
    assert "recommendation" in unreachable[0]


def test_high_cpu_produces_warning_with_risk_and_recommendation():
    save("last_scan.json", base_scan(node={"data": {
        "cpu": 0.95,
        "memory": {"total": 100, "used": 10},
        "rootfs": {"total": 100, "used": 10, "avail": 90}
    }}))

    findings = analyze_proxmox_cluster()

    cpu_findings = find(findings, "proxmox-cluster")
    assert any("CPU pressure" in f["issue"] for f in cpu_findings)
    for f in cpu_findings:
        assert "risk_score" in f
        assert "recommendation" in f


def test_disk_pressure_is_detected():
    save("last_scan.json", base_scan(node={"data": {
        "cpu": 0.1,
        "memory": {"total": 100, "used": 10},
        "rootfs": {"total": 100, "used": 95, "avail": 5}
    }}))

    findings = analyze_proxmox_cluster()

    disk_findings = [f for f in findings if "disk" in f["issue"].lower() or "storage" in f["issue"].lower()]
    assert len(disk_findings) == 1
    assert disk_findings[0]["severity"] in ("warning", "critical")


def test_stopped_vm_is_detected():
    save("last_scan.json", base_scan(qemu={"data": [
        {"vmid": 101, "name": "OPNsense", "status": "stopped", "cpu": 0, "mem": 0, "maxmem": 100}
    ]}))

    findings = analyze_proxmox_cluster()

    vm_findings = find(findings, "proxmox-vm")
    assert any("stopped" in f["issue"].lower() for f in vm_findings)
    assert any(f["severity"] == "critical" for f in vm_findings)


def test_high_cpu_vm_is_detected():
    save("last_scan.json", base_scan(qemu={"data": [
        {"vmid": 101, "name": "OPNsense", "status": "running", "cpu": 0.95, "mem": 10, "maxmem": 100}
    ]}))

    findings = analyze_proxmox_cluster()

    vm_findings = find(findings, "proxmox-vm")
    assert any("cpu" in f["issue"].lower() for f in vm_findings)


def test_high_memory_vm_is_detected():
    save("last_scan.json", base_scan(qemu={"data": [
        {"vmid": 101, "name": "OPNsense", "status": "running", "cpu": 0.1, "mem": 95, "maxmem": 100}
    ]}))

    findings = analyze_proxmox_cluster()

    vm_findings = find(findings, "proxmox-vm")
    assert any("memory" in f["issue"].lower() for f in vm_findings)


def test_failed_backup_is_detected():
    save("last_scan.json", base_scan(tasks={"data": [
        {"type": "vzdump", "status": "job errors", "starttime": 100, "endtime": 200}
    ]}))

    findings = analyze_proxmox_cluster()

    backup_findings = find(findings, "proxmox-backup")
    assert len(backup_findings) == 1
    assert backup_findings[0]["severity"] == "critical"


def test_successful_backup_is_not_flagged():
    save("last_scan.json", base_scan(tasks={"data": [
        {"type": "vzdump", "status": "OK", "starttime": 100, "endtime": 200}
    ]}))

    findings = analyze_proxmox_cluster()

    assert find(findings, "proxmox-backup") == []


def test_no_backup_history_produces_warning():
    save("last_scan.json", base_scan(tasks={"data": []}))

    findings = analyze_proxmox_cluster()

    backup_findings = find(findings, "proxmox-backup")
    assert len(backup_findings) == 1
    assert backup_findings[0]["severity"] == "warning"


def test_inactive_network_interface_is_detected():
    save("last_scan.json", base_scan(network={"data": [
        {"iface": "nic0", "exists": 1, "active": 0}
    ]}))

    findings = analyze_proxmox_cluster()

    net_findings = find(findings, "proxmox-network")
    assert len(net_findings) == 1


def test_all_healthy_produces_single_info_finding():
    save("last_scan.json", base_scan(tasks={"data": [
        {"type": "vzdump", "status": "OK", "starttime": 100, "endtime": 200}
    ]}))

    findings = analyze_proxmox_cluster()

    assert len(findings) == 1
    assert findings[0]["service"] == "proxmox-health-score"
    assert findings[0]["severity"] == "info"
