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
        # JARVIS P13 audit fix 2026-08-24: pass host metrics through —
        # health_observatory extracts snapshot["host"]["cpu_percent"] etc.
        # but scan() dropped the key, so host_*_pct series were all-zero.
        "host": inventory.get("host", {}),
        "proxmox": proxmox_status()
    }

    save(
        "last_scan.json",
        report
    )

    return report


if __name__ == "__main__":
    print(scan())
