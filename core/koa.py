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

    # Local model fabric status (owner directive: zero third-party providers).
    # Replaces the former api.deepseek.com reachability probe -- the fabric is
    # now the two self-hosted nodes (VM104 GPU ollama, VM112 CPU llama.cpp).
    local_fabric = {"status": "unknown", "nodes": {}}
    try:
        import requests

        ollama_up = False
        try:
            ollama_up = requests.get(
                "http://localhost:11434/api/tags", timeout=3
            ).status_code == 200
        except Exception:
            pass
        local_fabric["nodes"]["vm104-gpu"] = "up" if ollama_up else "down"

        cpu_up = False
        try:
            cpu_up = requests.get(
                "http://192.168.1.242:5001/health", timeout=3
            ).status_code == 200
        except Exception:
            pass
        local_fabric["nodes"]["vm112-cpu"] = "up" if cpu_up else "down"

        local_fabric["status"] = "online" if ollama_up else "degraded"
    except Exception:
        local_fabric["status"] = "unreachable"

    return {
        "appliance": {
            "name": "Kai Operations Appliance (KOA)",
            "version": "1.0",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        "resources": resources,
        "services": services,
        "docker": docker_containers,
        "local_fabric": local_fabric,
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
