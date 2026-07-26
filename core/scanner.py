from core.inventory import collect
from core.memory import save
from datetime import datetime


def scan():

    inventory = collect()

    report = {
        "scan_time": datetime.now().isoformat(),
        "hostname": inventory["hostname"],
        "docker": inventory.get("docker", {}),
    }

    save(
        "last_scan.json",
        report
    )

    return report


if __name__ == "__main__":
    print(scan())
