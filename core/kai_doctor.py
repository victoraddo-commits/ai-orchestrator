"""kai doctor — KAI self-diagnostic (§23).

Inspects core subsystems and reports one of:
healthy · degraded · broken · missing · blocked · unknown
with a remediation note for anything not healthy.

    python -m core.kai_doctor            # human/JSON output
    from core.kai_doctor import summary  # programmatic
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from collections import Counter

MEM = os.environ.get("KAI_MEMORY_DIR", "/opt/ai-orchestrator/memory")
CORE = os.environ.get("KAI_CORE_DIR", "/opt/ai-orchestrator/core")


def _sh(cmd, timeout=4) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _port(host, port, timeout=1.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _file(*parts) -> str:
    return os.path.join(*parts)


def summarize(checks: list) -> dict:
    counts = Counter(c["status"] for c in checks)
    return {
        "checks": checks,
        "summary": dict(counts),
        "healthy": all(c["status"] == "healthy" for c in checks),
        "unhealthy": [c["component"] for c in checks if c["status"] != "healthy"],
    }


def run_checks() -> list:
    out = []

    def add(component, status, detail="", remediation=""):
        out.append({"component": component, "status": status,
                    "detail": detail, "remediation": remediation})

    # Core orchestrator
    sched = _sh(["systemctl", "is-active", "kai-scheduler.service"])
    add("orchestrator", "healthy" if sched == "active" else "broken",
        f"kai-scheduler={sched or 'unknown'}", "systemctl restart kai-scheduler")

    # Command Center API
    add("command-center-api", "healthy" if _port("127.0.0.1", 8000) else "broken",
        ":8000", "systemctl restart ai-orchestrator-api")

    # Buses / engines (import-ability)
    for comp, mod in (("command-bus", "core.command_bus"),
                      ("event-bus", "core.kai_event_bus"),
                      ("mission-engine", "core.kai_missions"),
                      ("second-brain", "core.second_brain.router")):
        try:
            importlib.import_module(mod)
            add(comp, "healthy", mod)
        except Exception as e:  # noqa: BLE001
            add(comp, "broken", f"{mod}: {type(e).__name__}", "deploy module to runner")

    # Memory state
    for comp, fn in (("missions", "missions.json"), ("workers", "workers.json")):
        p = _file(MEM, fn)
        add(comp, "healthy" if os.path.exists(p) else "degraded",
            p, "ensure scheduler running")

    # Knowledge Fabric
    kf = _file(MEM, "knowledge_fabric.db")
    add("knowledge-fabric", "healthy" if os.path.exists(kf) else "degraded",
        kf, "create a document to initialize")

    # Telegram
    jk = _sh(["systemctl", "is-active", "juris-kai.service"])
    add("telegram", "healthy" if jk == "active" else "degraded",
        f"juris-kai={jk or 'unknown'}", "systemctl restart juris-kai")

    # Vault (machine plane — known external dependency)
    vault_up = _port("192.168.1.107", 8443, timeout=1.0)
    add("vault", "healthy" if vault_up else "blocked",
        "machine plane :8443 (TLS)", "provide Kai Vault credential")

    # Model fabric (provider routing module)
    try:
        importlib.import_module("core.ai.ai_router")
        add("model-fabric", "healthy", "core.ai.ai_router")
    except Exception as e:  # noqa: BLE001
        add("model-fabric", "degraded", f"{type(e).__name__}", "check ai_router")

    return out


def summary() -> dict:
    return summarize(run_checks())


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
