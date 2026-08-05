"""Phase 17Q: Kai Operations Appliance (KOA) — dedicated ops/infrastructure
management machine.

Bundles Kai's monitoring, remediation, learning, and dashboard into a
self-contained appliance configuration.  Designed to run on a dedicated
machine that manages the entire homelab infrastructure.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def get_appliance_status():
    """One-call status snapshot for the KOA dashboard."""

    # System health
    from core.health import analyze as health_analyze
    health = health_analyze()

    # Proxmox nodes
    from core.proxmox_monitor import collect_all_nodes
    proxmox = collect_all_nodes()

    # Running services
    services = {}
    for svc in [
        "ai-orchestrator", "ai-orchestrator-api",
        "ai-orchestrator-telegram", "law-tutor-bot", "susu-bot",
    ]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=5,
            )
            services[svc] = result.stdout.strip()
        except Exception:
            services[svc] = "unknown"

    # Docker
    docker_containers = []
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split(" ", 1)
                docker_containers.append({"name": parts[0], "status": parts[1] if len(parts) > 1 else "?"})
    except Exception:
        pass

    # Resource usage
    import psutil
    resources = {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "cpu_count": psutil.cpu_count(),
        "memory_percent": psutil.virtual_memory().percent,
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "memory_available_gb": round(psutil.virtual_memory().available / (1024**3), 1),
        "disk_percent": psutil.disk_usage("/").percent,
        "disk_total_gb": round(psutil.disk_usage("/").total / (1024**3), 1),
        "disk_free_gb": round(psutil.disk_usage("/").free / (1024**3), 1),
    }

    # gwen3 GPU status
    gwen3 = {"status": "unknown"}
    try:
        import requests
        api_key = os.environ.get("VLLM_QWEN3_CODER_API_KEY", "")
        base_url = os.environ.get("VLLM_QWEN3_CODER_BASE_URL", "")
        if api_key and base_url:
            resp = requests.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5,
            )
            gwen3["status"] = "online" if resp.status_code == 200 else f"error({resp.status_code})"
            if resp.status_code == 200:
                gwen3["models"] = [m.get("id") for m in resp.json().get("data", [])]
    except Exception:
        gwen3["status"] = "unreachable"

    return {
        "appliance": {
            "name": "Kai Operations Appliance (KOA)",
            "version": "1.0",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        "resources": resources,
        "services": services,
        "docker": docker_containers,
        "gwen3": gwen3,
        "proxmox": proxmox,
        "health": health,
    }


def generate_koa_systemd():
    """Generate the systemd service file for KOA."""
    return """[Unit]
Description=Kai Operations Appliance (KOA)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/project/ai-orchestrator
EnvironmentFile=/project/ai-orchestrator/.env
ExecStart=/project/ai-orchestrator/.venv/bin/python -m core.koa

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""


def run_koa():
    """Main KOA entry point — starts all Kai services."""
    services = [
        "ai-orchestrator",
        "ai-orchestrator-api",
        "ai-orchestrator-telegram",
        "law-tutor-bot",
        "susu-bot",
    ]
    started = []
    for svc in services:
        try:
            subprocess.run(
                ["systemctl", "start", f"{svc}.service"],
                capture_output=True, timeout=30,
            )
            started.append(f"{svc}: started")
        except Exception as e:
            started.append(f"{svc}: {e}")

    return {"koa": "Kai Operations Appliance", "services": started}
