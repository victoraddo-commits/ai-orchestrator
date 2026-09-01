from core.inventory import collect
from core.memory import save
from tools.proxmox import status as proxmox_status, status_b
from datetime import datetime


def scan():

    inventory = collect()

    # Primary: Proxmox B (all backup jobs run here) — direct LAN at 192.168.1.109
    proxmox_b = status_b()

    report = {
        "scan_time": datetime.now().isoformat(),
        "hostname": inventory["hostname"],
        "docker": inventory.get("docker", {}),
        "host": inventory.get("host", {}),
        # Primary proxmox key: B (all backup jobs run on B)
        "proxmox": proxmox_b,
        # Also record A for reference
        "proxmox_a": proxmox_status(),
    }

    save(
        "last_scan.json",
        report
    )

    return report


if __name__ == "__main__":
    print(scan())
