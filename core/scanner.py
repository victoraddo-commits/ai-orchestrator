from core.inventory import collect
from core.memory import save
from tools.proxmox import status as proxmox_status
from datetime import datetime


def scan():

    inventory = collect()

    report = {
        "scan_time": datetime.now().isoformat(),
        "hostname": inventory["hostname"],
        "docker": inventory.get("docker", {}),
        "proxmox": proxmox_status()
    }

    save(
        "last_scan.json",
        report
    )

    return report


if __name__ == "__main__":
    print(scan())
