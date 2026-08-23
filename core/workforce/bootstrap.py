"""Self-registration of existing orchestrator components into the workforce
registry. Called once per run_cycle(); every step is exception-guarded so a
registry problem can never break the pipeline (same convention as the
budget/notification safety wrappers in orchestrator_cycle.py).
"""
from __future__ import annotations

import inspect
import os

import core.ai_provider as ai_provider
from core.workforce import registry
from core.logger import info as _log

# Dev-only provider names — registered development/temporary/no-secrets no
# matter what their provider entry claims (Ox Alpha rule).
_DEV_ONLY_PROVIDERS = {"ox_alpha"}

AGENT_ROLE_TASK_TYPES = {}  # populated lazily in sync_roles


def _slug(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _discover_role_task_types() -> dict:
    """agent_roles.py has no AGENT_ROLES dict — it is four role functions
    that each pin a task_type. Discover (role -> [task_type]) by inspecting
    those functions, so the registry follows the module without a second
    source of truth."""
    if AGENT_ROLE_TASK_TYPES:
        return AGENT_ROLE_TASK_TYPES
    try:
        from core.ai import agent_roles
    except Exception as error:
        _log(f"workforce bootstrap: role import failed: {type(error).__name__}")
        return {}
    discovered = {}
    for name, fn in vars(agent_roles).items():
        if not inspect.isfunction(fn) or fn.__module__ != agent_roles.__name__:
            continue
        try:
            source = inspect.getsource(fn)
        except OSError:
            continue
        for line in source.splitlines():
            # Real source is e.g. `return delegate(description,
            # task_type="planning", **kwargs)` — match anywhere in the line.
            marker = "task_type="
            idx = line.find(marker)
            if idx != -1:
                value = line[idx + len(marker):].strip()
                # take the first quoted token: "planning", ...
                if value[:1] in "\"'":
                    discovered[name] = [value[1:].split(value[0])[0]]
                break
    return discovered


def sync_providers() -> int:
    """Register every provider from the existing ai_provider registry."""
    try:
        providers = ai_provider.list_providers() or {}
    except Exception as error:  # never break the cycle
        _log(f"workforce bootstrap: provider sync failed: {type(error).__name__}")
        return 0
    count = 0
    for name, meta in providers.items():
        wid = f"provider:{name}"
        is_dev = _slug(name) in _DEV_ONLY_PROVIDERS
        caps = [
            "generate" if c in ("text_task", "coding_agent", "coding") else c
            for c in (meta.get("capabilities") or [])
        ]
        registry.register(registry.WorkerRecord(
            worker_id=wid,
            kind="provider",
            capabilities=caps or ["generate"],
            permissions={
                "secrets": [] if is_dev else [f"ai-orchestrator/providers/{_slug(name)}"],
                "network": ["provider-apis"],
                "filesystem": [],
            },
            limits={"max_concurrency": 1, "timeout_seconds": 600},
            environment="development" if is_dev else "production",
            temporary=is_dev,
            metadata={"cost_tier": meta.get("cost_tier", "unknown"),
                      "description": meta.get("description", "")[:120]},
        ))
        count += 1
    return count


def sync_pool_slots(max_concurrent: int) -> int:
    """Register the ThreadPoolExecutor build slots as pool workers."""
    count = 0
    for i in range(max(1, int(max_concurrent))):
        registry.register(registry.WorkerRecord(
            worker_id=f"pool-worker-{i}",
            kind="pool_worker",
            capabilities=["generate", "review", "deploy"],
            permissions={"secrets": [], "network": ["provider-apis"],
                         "filesystem": ["sandbox"]},
            limits={"max_concurrency": 1, "timeout_seconds": 2400},
            metadata={"slot": i},
        ))
        count += 1
    return count


def sync_roles() -> int:
    """Register agent_roles.py semantic roles as role workers."""
    global AGENT_ROLE_TASK_TYPES
    discovered = _discover_role_task_types()
    if not discovered:
        return 0
    AGENT_ROLE_TASK_TYPES = discovered
    count = 0
    for role_name, task_types in AGENT_ROLE_TASK_TYPES.items():
        registry.register(registry.WorkerRecord(
            worker_id=f"role:{role_name}",
            kind="role",
            capabilities=task_types or ["planning"],
            permissions={"secrets": [], "network": ["provider-apis"],
                         "filesystem": []},
            limits={"max_concurrency": 4, "timeout_seconds": 600},
            metadata={},
        ))
        count += 1
    return count


def sync_local_models() -> int:
    """Probe Ollama on Proxmox B (:11434) and register local models.
    Best-effort: offline Ollama just means zero registrations."""
    import json as _json
    import urllib.request
    host = os.environ.get("OLLAMA_HOST", "http://192.168.1.113:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            tags = _json.loads(resp.read().decode()).get("models", [])
    except Exception:
        return 0
    count = 0
    for model in tags:
        mname = _slug(model.get("name", ""))
        if not mname:
            continue
        registry.register(registry.WorkerRecord(
            worker_id=f"local:{mname}",
            kind="local_model",
            capabilities=["generate", "review", "classification"],
            permissions={"secrets": [], "network": ["lan-ollama"],
                         "filesystem": []},
            limits={"max_concurrency": 2, "timeout_seconds": 300},
            metadata={"endpoint": host, "size_bytes": model.get("size")},
        ))
        count += 1
    return count


def run_full_sync(max_concurrent_builds: int = 4) -> dict:
    """All syncs, exception-guarded individually. Returns counts."""
    results = {}
    for label, fn, arg in (
        ("providers", sync_providers, None),
        ("pool_slots", sync_pool_slots, max_concurrent_builds),
        ("roles", sync_roles, None),
        ("local_models", sync_local_models, None),
    ):
        try:
            results[label] = fn(arg) if arg is not None else fn()
        except Exception as error:
            _log(f"workforce bootstrap: {label} sync failed: {type(error).__name__}")
            results[label] = 0
    try:
        registry.deregister_expired()
    except Exception:
        pass
    return results
