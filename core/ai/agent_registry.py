"""AI Agent Registry — management layer above provider definitions.

Tracks agents (model + version + GPU + cost + benchmarks) as higher-level
abstractions over raw provider API endpoints. Each agent maps to a provider
key in core.ai_provider.

Features:
  - Agent CRUD with persistence in memory/agents.json
  - Enable/disable with automatic routing integration
  - Benchmark recording (latency, accuracy, tool-use success rate)
  - Cost history tracking (per-run cost records)
  - Health test endpoint (single quick delegate call)
  - Fallback chain per agent

Storage: memory/agents.json (atomic writes, same pattern as memory.py/secrets.py).
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

STORAGE_PATH = Path(__file__).parent.parent.parent / "memory" / "agents.json"
_write_lock = Lock()


def _load() -> dict:
    """Load the agent registry. Returns empty dict on any error."""
    try:
        if STORAGE_PATH.exists():
            return json.loads(STORAGE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"schema_version": 1, "agents": {}}


def _save(data: dict) -> None:
    """Atomic write — temp file + os.replace under a lock."""
    with _write_lock:
        try:
            STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            tmp = STORAGE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.chmod(0o600)
            tmp.replace(STORAGE_PATH)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Agent schema (default fields for a new agent)
# ---------------------------------------------------------------------------

def _new_agent(
    agent_id: str,
    name: str,
    provider_key: str,
    model_name: str,
    model_version: str = "",
    gpu_type: str = "",
    gpu_memory_gb: int = 0,
    cost_per_hour: float = 0.0,
    capabilities: list[str] | None = None,
    fallback_chain: list[str] | None = None,
    description: str = "",
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": agent_id,
        "name": name,
        "provider_key": provider_key,
        "model_name": model_name,
        "model_version": model_version,
        "gpu_type": gpu_type,
        "gpu_memory_gb": gpu_memory_gb,
        "cost_per_hour": cost_per_hour,
        "capabilities": capabilities or [],
        "description": description,
        "status": "active",
        "fallback_chain": fallback_chain or [],
        "benchmarks": {
            "latency_ms": 0,
            "accuracy_score": 0,
            "tool_use_success_rate": 0,
            "last_benchmark_at": None,
        },
        "cost_history": [],
        "performance_history": [],
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def register_agent(
    agent_id: str,
    name: str,
    provider_key: str,
    model_name: str,
    model_version: str = "",
    gpu_type: str = "",
    gpu_memory_gb: int = 0,
    cost_per_hour: float = 0.0,
    capabilities: list[str] | None = None,
    fallback_chain: list[str] | None = None,
    description: str = "",
) -> dict:
    """Register (or update) an agent in the registry.

    Returns the agent record.  If the agent already exists, its metadata is
    updated but status, benchmarks, and history are preserved.
    """
    data = _load()
    agents = data.setdefault("agents", {})

    if agent_id in agents:
        # Update existing agent's metadata
        existing = agents[agent_id]
        existing["name"] = name
        existing["provider_key"] = provider_key
        existing["model_name"] = model_name
        existing["model_version"] = model_version
        existing["gpu_type"] = gpu_type
        existing["gpu_memory_gb"] = gpu_memory_gb
        existing["cost_per_hour"] = cost_per_hour
        existing["capabilities"] = capabilities or existing.get("capabilities", [])
        existing["fallback_chain"] = fallback_chain or existing.get("fallback_chain", [])
        existing["description"] = description
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        agents[agent_id] = _new_agent(
            agent_id=agent_id,
            name=name,
            provider_key=provider_key,
            model_name=model_name,
            model_version=model_version,
            gpu_type=gpu_type,
            gpu_memory_gb=gpu_memory_gb,
            cost_per_hour=cost_per_hour,
            capabilities=capabilities,
            fallback_chain=fallback_chain,
            description=description,
        )

    _save(data)
    return agents[agent_id]


def get_agent(agent_id: str) -> dict | None:
    """Get a single agent by ID."""
    data = _load()
    return data.get("agents", {}).get(agent_id)


def list_agents(status: str | None = None) -> list[dict]:
    """List all registered agents, optionally filtered by status.

    Returns sorted by name for predictable ordering.
    """
    data = _load()
    agents = list(data.get("agents", {}).values())
    if status:
        agents = [a for a in agents if a.get("status") == status]
    return sorted(agents, key=lambda a: a["name"])


def delete_agent(agent_id: str) -> bool:
    """Remove an agent from the registry. Returns True if found."""
    data = _load()
    if agent_id not in data.get("agents", {}):
        return False
    del data["agents"][agent_id]
    _save(data)
    return True


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------

def enable_agent(agent_id: str) -> bool:
    """Set agent status to 'active'. Returns True if agent was found."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False
    agent["status"] = "active"
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    _sync_agent_enable_state(agent_id, enabled=True)
    return True


def disable_agent(agent_id: str) -> bool:
    """Set agent status to 'disabled'. Returns True if agent was found."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False
    agent["status"] = "disabled"
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    _sync_agent_enable_state(agent_id, enabled=False)
    return True


def set_maintenance(agent_id: str) -> bool:
    """Set agent status to 'maintenance'."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False
    agent["status"] = "maintenance"
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


# ---------------------------------------------------------------------------
# Benchmarks & testing
# ---------------------------------------------------------------------------

def record_benchmark(
    agent_id: str,
    latency_ms: int,
    accuracy_score: float = 0,
    tool_use_success_rate: float = 0,
) -> bool:
    """Record benchmark results for an agent."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False
    agent["benchmarks"] = {
        "latency_ms": latency_ms,
        "accuracy_score": round(accuracy_score, 4),
        "tool_use_success_rate": round(tool_use_success_rate, 4),
        "last_benchmark_at": datetime.now(timezone.utc).isoformat(),
    }
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


def test_agent(agent_id: str, timeout: int = 30) -> dict:
    """Run a quick health/benchmark test against the agent's provider.

    Sends a simple prompt via ai_router.delegate() and measures latency.
    Uses text_task capability to avoid tool-use overhead.

    Returns:
        {"ok": bool, "provider": str, "latency_ms": int, "response": str, "error": str}
    """
    agent = get_agent(agent_id)
    if not agent:
        return {"ok": False, "error": f"Agent not found: {agent_id}"}

    provider_key = agent.get("provider_key", "")
    if not provider_key:
        return {"ok": False, "error": f"Agent has no provider_key"}

    # Check if provider exists and is available
    try:
        from core.ai_provider import get_provider
        provider = get_provider(provider_key)
        if not provider:
            return {"ok": False, "error": f"Provider not found: {provider_key}"}
        if not provider.get("enabled"):
            return {"ok": False, "error": f"Provider disabled: {provider_key}"}
        if not provider.get("available_fn", lambda: False)():
            return {"ok": False, "error": f"Provider unavailable: {provider_key}"}
    except Exception as e:
        return {"ok": False, "error": f"Provider check failed: {e}"}

    # Run a quick test
    start = time.time()
    try:
        from core.ai.ai_router import delegate
        result = delegate(
            "Reply with exactly the single word: healthy",
            task_type="classification",
            capability="text_task",
            timeout=timeout,
        )
        latency = int((time.time() - start) * 1000)
        response_text = result.get("response", "")[:200]
        actual_provider = result.get("provider", provider_key)

        # Record benchmark
        is_healthy = "healthy" in response_text.lower()
        record_benchmark(
            agent_id,
            latency_ms=latency,
            accuracy_score=1.0 if is_healthy else 0.0,
            tool_use_success_rate=0,  # text_task has no tool use
        )

        return {
            "ok": True,
            "provider": actual_provider,
            "latency_ms": latency,
            "response": response_text,
            "healthy": is_healthy,
        }
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {
            "ok": False,
            "provider": provider_key,
            "latency_ms": latency,
            "error": str(e)[:500],
        }


# ---------------------------------------------------------------------------
# Cost & performance history
# ---------------------------------------------------------------------------

def record_cost(agent_id: str, run_id: str, cost_usd: float, tokens_in: int, tokens_out: int,
                task_type: str = "", project: str = "") -> bool:
    """Record a cost entry for an agent run."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False

    entry = {
        "run_id": run_id,
        "cost_usd": round(cost_usd, 6),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "task_type": task_type,
        "project": project,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    agent["cost_history"].append(entry)
    # Keep last 1000 entries
    if len(agent["cost_history"]) > 1000:
        agent["cost_history"] = agent["cost_history"][-1000:]
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


def record_performance(agent_id: str, run_id: str, latency_ms: int, success: bool,
                       task_type: str = "", error_type: str = "") -> bool:
    """Record a performance data point for an agent run."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False

    entry = {
        "run_id": run_id,
        "latency_ms": latency_ms,
        "success": success,
        "task_type": task_type,
        "error_type": error_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    agent["performance_history"].append(entry)
    if len(agent["performance_history"]) > 1000:
        agent["performance_history"] = agent["performance_history"][-1000:]
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


def get_cost_history(agent_id: str, limit: int = 50) -> list[dict]:
    """Get recent cost entries for an agent, newest first."""
    agent = get_agent(agent_id)
    if not agent:
        return []
    history = agent.get("cost_history", [])
    return history[-limit:][::-1]


def get_performance_history(agent_id: str, limit: int = 50) -> list[dict]:
    """Get recent performance entries for an agent, newest first."""
    agent = get_agent(agent_id)
    if not agent:
        return []
    history = agent.get("performance_history", [])
    return history[-limit:][::-1]


def get_agent_stats(agent_id: str) -> dict:
    """Get aggregate stats for an agent (success rate, avg latency, total cost)."""
    agent = get_agent(agent_id)
    if not agent:
        return {}

    perf = agent.get("performance_history", [])
    costs = agent.get("cost_history", [])

    total_runs = len(perf)
    successful = sum(1 for p in perf if p.get("success"))
    total_cost = sum(c.get("cost_usd", 0) for c in costs)
    avg_latency = (
        int(sum(p.get("latency_ms", 0) for p in perf) / total_runs)
        if total_runs > 0 else 0
    )

    return {
        "agent_id": agent_id,
        "name": agent.get("name", ""),
        "status": agent.get("status", ""),
        "total_runs": total_runs,
        "successful_runs": successful,
        "success_rate": round(successful / total_runs, 4) if total_runs > 0 else 0,
        "avg_latency_ms": avg_latency,
        "total_cost_usd": round(total_cost, 6),
        "benchmarks": agent.get("benchmarks", {}),
    }


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

def set_fallback_chain(agent_id: str, chain: list[str]) -> bool:
    """Set the fallback chain for an agent."""
    data = _load()
    agent = data.get("agents", {}).get(agent_id)
    if not agent:
        return False
    agent["fallback_chain"] = chain
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


def get_fallback_chain(agent_id: str) -> list[str]:
    """Get the fallback chain for an agent, with disabled agents filtered out."""
    agent = get_agent(agent_id)
    if not agent:
        return []
    chain = agent.get("fallback_chain", [])
    # Filter out disabled agents from the chain
    return [aid for aid in chain
            if _agent_is_available(aid)]


# ---------------------------------------------------------------------------
# Initialization — register known agents from ai_provider
# ---------------------------------------------------------------------------

def _agent_is_available(agent_id: str) -> bool:
    """Check if an agent is available (exists + active)."""
    agent = get_agent(agent_id)
    return agent is not None and agent.get("status") == "active"


def _sync_agent_enable_state(agent_id: str, enabled: bool) -> None:
    """Sync agent enable/disable to the underlying provider's enabled state."""
    agent = get_agent(agent_id)
    if not agent:
        return
    provider_key = agent.get("provider_key")
    if not provider_key:
        return
    try:
        from core.ai_provider import set_provider_enabled
        set_provider_enabled(provider_key, enabled)
    except ImportError:
        pass


def bootstrap_default_agents() -> int:
    """Seed the agent registry with agents matching known providers.

    Idempotent — only creates agents that don't already exist.  Returns the
    number of agents created (or already existing).

    Each agent maps to an existing provider in core.ai_provider.
    """
    from core.ai_provider import list_providers as _list_providers

    providers = _list_providers()
    created = 0

    default_agents = {
        "gemini": {
            "name": "Gemini",
            "model_name": "Gemini 2.5 Pro",
            "model_version": "Google AI",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["text_task"],
            "fallback_chain": ["geminix", "deepseek_native_flash"],
            "description": "Google Gemini — text_task, healthy (credit reloaded). Google billing",
        },
        "deepseek_native_flash": {
            "name": "DeepSeek V4 Flash (Native)",
            "model_name": "DeepSeek V4 Flash",
            "model_version": "api.deepseek.com",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["text_task"],
            "fallback_chain": ["deepseek_native_pro", "omniroute_deepseek_flash"],
            "description": "DeepSeek V4 Flash via native api.deepseek.com — fast, free_or_low_cost text_task",
        },
        "deepseek_native_pro": {
            "name": "DeepSeek V4 Pro (Native)",
            "model_name": "DeepSeek V4 Pro",
            "model_version": "api.deepseek.com",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["text_task"],
            "fallback_chain": ["deepseek_native_flash", "omniroute_deepseek_flash"],
            "description": "DeepSeek V4 Pro via native api.deepseek.com — higher-quality text_task",
        },
        "groq": {
            "name": "Groq",
            "model_name": "Llama-4 Maverick",
            "model_version": "Groq Cloud",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["text_task"],
            "fallback_chain": ["gemini", "deepseek_native_flash"],
            "description": "Groq Llama-4 — healthy, free tier, fast inference",
        },
        "claude": {
            "name": "Claude (Direct)",
            "model_name": "Claude Fable 5",
            "model_version": "CloudCLI /api/agent",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["coding_agent", "text_task", "file_access"],
            "fallback_chain": ["omniroute", "gpuai_minimax"],
            "description": "Claude via direct CloudCLI subscription — out of credit currently, in tail position",
        },
        "omniroute": {
            "name": "OmniRoute",
            "model_name": "Claude Sonnet 5",
            "model_version": "localhost:20128",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["coding_agent", "text_task"],
            "fallback_chain": ["claude"],
            "description": "Self-hosted aggregator gateway on localhost:20128 — always-on fallback",
        },
        "gpuai_minimax": {
            "name": "GPU.ai Minimax M3",
            "model_name": "MiniMax M3",
            "model_version": "GPU.ai serverless",
            "gpu_type": "",
            "gpu_memory_gb": 0,
            "cost_per_hour": 0.0,
            "capabilities": ["coding_agent", "text_task"],
            "fallback_chain": [],
            "description": "MiniMax M3 (gpuai/minimax-m3) via GPU.ai serverless API — replaces opencode_minimax, OpenAI-compatible",
        },
    }

    for agent_id, agent_data in default_agents.items():
        if agent_id not in providers:
            continue  # skip agents whose provider isn't registered
        if get_agent(agent_id) is None:
            register_agent(
                agent_id=agent_id,
                name=agent_data["name"],
                provider_key=agent_id,
                model_name=agent_data["model_name"],
                model_version=agent_data["model_version"],
                gpu_type=agent_data["gpu_type"],
                gpu_memory_gb=agent_data["gpu_memory_gb"],
                cost_per_hour=agent_data["cost_per_hour"],
                capabilities=agent_data["capabilities"],
                fallback_chain=agent_data["fallback_chain"],
                description=agent_data["description"],
            )
            created += 1

    return created


# This module must NEVER import:
#   core.build_manager, core.approval, core.deployment_manager
