"""Unified Model Registry — KAI 2.0 (§75 #6, #7, #10).

A single, queryable projection of the Model Fabric. Each entry is a
provider/model with its **capabilities**, cost tier, routing roles,
availability, enabled state, and benchmark summary.

Read-only over existing sources of truth (``core.ai_provider``,
``core.ai.ai_router.ROLE_PROVIDERS``, ``core.provider_health_monitor``,
``core.ai.model_benchmarks``) — this module never competes with them; it
records what the fabric already knows so routing/inspection can be
evidence-based.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = 1

# Underlying artefact served by each local/ollama provider. This is the piece
# the fabric previously did not record in one place.
LOCAL_MODELS = {
    "kai_brain": "qwen3-coder:kai",
    "kai_coder": "qwen3-coder:kai",
    "kai_deep": "qwen3-coder:kai",
    "kai_small": "qwen2.5:1.5b",
    "local": "qwen3-coder:kai",
    "llama_coder_cpu": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "koboldcpp_cpu": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "koboldcpp_cpu_a": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "koboldcpp_cpu_b": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "qwen": "qwen2.5:7b",
}

# Declared model-level capabilities (beyond the transport-level capabilities
# that ai_provider derives: coding_agent / text_task / file_access).
MODEL_CAPABILITIES = {
    "qwen3-coder:kai": ["coding", "reasoning", "tool_use", "long_context", "architecture"],
    "qwen2.5:1.5b": ["fast", "lookup", "grounded"],
    "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf": ["coding", "fast", "cpu"],
    "qwen2.5:7b": ["classification", "reasoning", "fast"],
}

_ENDPOINTS = {
    "kai_brain": "http://localhost:11434",
    "kai_coder": "http://localhost:11434",
    "kai_deep": "http://localhost:11434",
    "kai_small": "http://localhost:11434",
    "local": "http://localhost:11434",
    "llama_coder_cpu": "http://192.168.1.242:5001",
    "koboldcpp_cpu": "http://192.168.1.242:5001",
    "koboldcpp_cpu_a": "http://192.168.1.242:5001",
    "koboldcpp_cpu_b": "http://192.168.1.242:5002",
    "qwen": "http://localhost:11434",
}

DEFAULT_SNAPSHOT = "model_registry.json"

# Physical local node behind each endpoint. This is the unit of failure
# diversity for §7: providers sharing a node do NOT survive that node going
# down, so a chain naming only VM104 providers is still a single point of
# failure.
NODE_LABELS = {
    "vm104-gpu": "VM104 (Tesla P40, ollama :11434)",
    "vm112-cpu": "VM112 (Xeon CPU, llama.cpp :5001/:5002)",
    "unknown": "unknown node",
}


def endpoint_for(name: str) -> str | None:
    """Return the HTTP endpoint backing ``name`` (None when unknown)."""
    return _ENDPOINTS.get(name)


def node_for(name: str) -> str:
    """Classify a provider's physical local node (vm104-gpu / vm112-cpu)."""
    ep = _ENDPOINTS.get(name) or ""
    if "192.168.1.242" in ep or ":5001" in ep or ":5002" in ep:
        return "vm112-cpu"
    if "11434" in ep or "localhost" in ep or "127.0.0.1" in ep:
        return "vm104-gpu"
    return "unknown"


def _roles_for(name: str, role_providers: dict) -> list:
    return sorted(role for role, chain in (role_providers or {}).items() if name in chain)


def _load_sources():
    """Lazily import the live fabric. Never raise — degrade to empty."""
    providers = {}
    roles = {}
    health = {}
    benchmarks = {}
    try:
        from core.ai_provider import list_providers
        providers = list_providers()
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.ai.ai_router import ROLE_PROVIDERS
        roles = ROLE_PROVIDERS
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.provider_health_monitor import provider_health
        health = provider_health.all_snapshots() if hasattr(provider_health, "all_snapshots") else {}
    except Exception:  # noqa: BLE001
        health = {}
    try:
        from core.ai.model_benchmarks import BenchmarkStore
        benchmarks = BenchmarkStore().all_summaries()
    except Exception:  # noqa: BLE001
        benchmarks = {}
    return providers, roles, health, benchmarks


def build_registry(providers=None, role_providers=None, health=None, benchmarks=None) -> dict:
    """Build the unified registry. All sources are injectable for tests."""
    if providers is None or role_providers is None:
        _p, _r, _h, _b = _load_sources()
        providers = _p if providers is None else providers
        role_providers = _r if role_providers is None else role_providers
        health = _h if health is None else health
        benchmarks = _b if benchmarks is None else benchmarks
    providers = providers or {}
    role_providers = role_providers or {}
    health = health or {}
    benchmarks = benchmarks or {}

    models = {}
    for name, entry in providers.items():
        artifact = LOCAL_MODELS.get(name)
        caps = sorted(set(list(entry.get("capabilities") or [])
                          + list(MODEL_CAPABILITIES.get(artifact, []))))
        models[name] = {
            "id": name,
            "kind": entry.get("kind", "unknown"),
            "model": artifact,
            "description": entry.get("description", ""),
            "capabilities": caps,
            "cost_tier": entry.get("cost_tier", "unknown"),
            "available": bool(entry.get("available", False)),
            "enabled": bool(entry.get("enabled", True)),
            "roles": _roles_for(name, role_providers),
            "endpoint": _ENDPOINTS.get(name),
            "health": health.get(name),
            "benchmark": benchmarks.get(name),
        }

    capabilities = {}
    for record in models.values():
        for cap in record["capabilities"]:
            capabilities.setdefault(cap, []).append(record["id"])
    for cap in capabilities:
        capabilities[cap] = sorted(capabilities[cap])

    return {
        "schema": SCHEMA,
        "generated_at": time.time(),
        "counts": {
            "models": len(models),
            "available": sum(1 for m in models.values() if m["available"]),
            "enabled": sum(1 for m in models.values() if m["enabled"]),
            "capabilities": len(capabilities),
        },
        "models": models,
        "capabilities": capabilities,
    }


def get(name: str, registry: dict = None) -> dict | None:
    registry = registry if registry is not None else build_registry()
    return registry["models"].get(name)


def by_capability(cap: str, registry: dict = None) -> list:
    registry = registry if registry is not None else build_registry()
    return registry["capabilities"].get(cap, [])


def by_role(role: str, registry: dict = None) -> list:
    registry = registry if registry is not None else build_registry()
    return sorted(n for n, m in registry["models"].items() if role in m["roles"])


def save_snapshot(memory_dir, registry: dict = None, filename: str = DEFAULT_SNAPSHOT) -> Path:
    registry = registry if registry is not None else build_registry()
    path = Path(memory_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def load_snapshot(memory_dir, filename: str = DEFAULT_SNAPSHOT) -> dict | None:
    path = Path(memory_dir) / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":  # pragma: no cover - manual snapshot tool
    mem = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory")
    reg = build_registry()
    out = save_snapshot(mem, reg)
    print(f"model registry: {reg['counts']} -> {out}")
