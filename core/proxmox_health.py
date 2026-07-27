from core.memory import load


def analyze_proxmox_cluster():

    findings = []

    state = load("last_scan.json")

    proxmox = state.get("proxmox", {})

    node = proxmox.get("node", {}).get("data", {})

    if not node:
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
            "issue": f"CPU pressure detected: {cpu:.2%}"
        })


    if memory_usage > 0.90:
        health_score -= 25

        findings.append({
            "severity": "warning",
            "service": "proxmox-cluster",
            "issue": f"Memory pressure detected: {memory_usage:.2%}"
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
            "issue": f"Stopped containers: {stopped}"
        })


    if health_score < 100:
        findings.append({
            "severity": "warning",
            "service": "proxmox-health-score",
            "issue": f"Health score degraded: {health_score}",
            "score": max(health_score, 0)
        })
    else:
        findings.append({
            "severity": "info",
            "service": "proxmox-health-score",
            "issue": "Healthy",
            "score": 100
        })


    return findings


if __name__ == "__main__":
    print(analyze_proxmox_cluster())
