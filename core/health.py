from core.memory import load
from core.docker_analyzer import analyze_docker
from core.service_monitor import check_services
from core.proxmox_health import analyze_proxmox_cluster


EXPECTED_SERVICES = (
    "pulse",
    "proxdash-backend",
    "proxdash-frontend",
    "it-manager",
    "it-manager-mailpit",
    "watchtower",
    "code-server",
    "airdrop-hunter-airdrop-hunter-1",
    "portfolio-portfolio-1",
    "docker-watcher",
    "door-bridge",
)


def analyze():

    findings = []

    state = load("system_state.json")

    docker = state.get(
        "docker",
        {}
    )


    if not docker.get("available"):

        findings.append({
            "severity": "critical",
            "service": "docker",
            "issue": "Docker unavailable"
        })

        return findings


    containers = docker.get(
        "containers",
        []
    )


    for service in EXPECTED_SERVICES:

        if service not in containers:

            findings.append({

                "severity": "warning",

                "service": service,

                "issue": f"Missing container: {service}"

            })


    if len(containers) == 0:

        findings.append({

            "severity": "critical",

            "service": "docker",

            "issue": "No containers detected"

        })


    docker_findings = analyze_docker()

    findings.extend(
        docker_findings
    )


    service_findings = check_services()

    findings.extend(
        service_findings
    )

    proxmox_findings = analyze_proxmox_cluster()

    findings.extend(
        proxmox_findings
    )

    return findings



if __name__ == "__main__":

    print(analyze())


def analyze_proxmox():

    from core.memory import load

    findings = []

    state = load("last_scan.json")

    proxmox = state.get(
        "proxmox",
        {}
    )


    node = proxmox.get(
        "node",
        {}
    ).get(
        "data",
        {}
    )


    if not node:
        return findings


    cpu = node.get(
        "cpu",
        0
    )


    if cpu > 0.90:

        findings.append({

            "severity": "warning",
            "service": "proxmox-node",
            "issue": f"High CPU usage {cpu}"

        })


    memory = node.get(
        "memory",
        {}
    )


    total = memory.get(
        "total",
        1
    )

    used = memory.get(
        "used",
        0
    )


    usage = used / total


    if usage > 0.90:

        findings.append({

            "severity": "warning",
            "service": "proxmox-memory",
            "issue": f"High memory usage {usage:.2%}"

        })


    lxc = proxmox.get(
        "lxc",
        {}
    ).get(
        "data",
        []
    )


    for container in lxc:

        if container.get("status") != "running":

            findings.append({

                "severity": "critical",
                "service": container.get("name"),
                "issue": "LXC container stopped"

            })


    return findings
