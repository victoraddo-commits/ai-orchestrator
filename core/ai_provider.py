"""AI provider registry — local-only model fabric.

Owner directive: zero third-party/cloud providers. Every entry here is a
local model served by Kai's own infrastructure (VM104 Tesla P40 ollama,
VM112 CPU llama.cpp) with ``kind="local"``. There is intentionally no cloud
registration path; ``register_provider`` stays generic, but this module
registers only local providers and a test
(``tests/test_local_only_fabric.py``) asserts that hard invariant.
"""

import json
import os
from pathlib import Path

# Load .env file to ensure environment variables are available for provider checks
try:
    from dotenv import load_dotenv
    # Load from ai-orchestrator root
    ai_orch_path = Path(__file__).parent.parent
    env_path = ai_orch_path / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # dotenv not available, continue with existing environment
    pass

import core.local_coding_bridge as local_coding_bridge


_PROVIDERS = {}

# Persisted enabled/disabled state so toggles survive process restarts.
# Stored in the memory/ directory alongside other runtime state.
_PROVIDER_STATE_PATH = Path(__file__).parent.parent / "memory" / "provider_state.json"

def _load_provider_state():
    """Load persisted enabled/disabled overrides, if any."""
    try:
        if _PROVIDER_STATE_PATH.exists():
            return json.loads(_PROVIDER_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}

def _save_provider_state(state):
    """Atomically persist enabled/disabled state."""
    tmp = _PROVIDER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(_PROVIDER_STATE_PATH)

# Static cost classification per provider -- assigned at registration, never
# computed. Kept for wire-format/back-compat; every local provider is "free"
# (self-hosted, $0/token). The paid/credit tiers remain valid values so the
# generic register_provider contract and its validation tests are unchanged.
COST_TIERS = ("free", "free_or_low_cost", "paid")


def register_provider(name, run_coding_task=None, run_text_task=None, available_fn=None, kind="cloud", description="", cost_tier="free_or_low_cost"):
    if cost_tier not in COST_TIERS:
        raise ValueError(f"cost_tier must be one of {COST_TIERS}, got {cost_tier!r}")

    capabilities = []
    if run_coding_task is not None:
        capabilities.append("coding_agent")
        # 17R: file-access-aware routing -- coding agents inherently have
        # filesystem access; text_task providers do not. This is registered
        # as an explicit capability so delegate() can filter/deprioritize on
        # requires_file_access without guessing from the run_* function shape.
        capabilities.append("file_access")
    if run_text_task is not None:
        capabilities.append("text_task")

    _persisted = _load_provider_state()

    _PROVIDERS[name] = {
        "run_coding_task": run_coding_task,
        "run_text_task": run_text_task,
        "available_fn": available_fn,
        "kind": kind,
        "description": description,
        "capabilities": capabilities,
        "cost_tier": cost_tier,
        "enabled": _persisted.get(name, True),  # default on unless explicitly disabled
    }


def get_provider(name):
    return _PROVIDERS.get(name)


def list_providers():
    return {
        name: {
            "kind": entry["kind"],
            "description": entry["description"],
            "available": bool(entry["available_fn"]()),
            "enabled": entry.get("enabled", True),
            "capabilities": entry["capabilities"],
            "cost_tier": entry["cost_tier"],
        }
        for name, entry in _PROVIDERS.items()
    }


def deregister_provider(name: str) -> bool:
    """Remove a provider from the registry and persisted state.

    Returns True if the provider was found and removed, False if not found.
    This only removes from the registry -- callers must also remove the
    provider from ai_router.ROLE_PROVIDERS and any other routing structures.
    """
    entry = _PROVIDERS.pop(name, None)
    if entry is None:
        return False
    state = _load_provider_state()
    state.pop(name, None)
    _save_provider_state(state)
    return True


def set_provider_enabled(name: str, enabled: bool) -> bool:
    """Toggle a provider on or off.  Persisted to memory/provider_state.json
    so the setting survives process restarts.  Returns True if the provider
    exists, False if not found."""
    entry = _PROVIDERS.get(name)
    if entry is None:
        return False
    entry["enabled"] = enabled
    state = _load_provider_state()
    state[name] = enabled
    _save_provider_state(state)
    return True


def get_provider_enabled(name: str) -> bool:
    """Check if a provider is enabled (default True for all providers)."""
    entry = _PROVIDERS.get(name)
    return entry.get("enabled", True) if entry else False


# ── §7 Local Model Fabric providers ─────────────────────────────────────────
# ai_router.ROLE_PROVIDERS routes every core role to kai_brain/kai_coder/
# kai_deep (VM104 P40 ollama :11434) and llama_coder_cpu (VM112 llama.cpp
# :5001). These registrations back the primary local route and the
# VM104→VM112 node failover (§7 model diversity). 100% local — no cloud.
_KAI_MODEL = "qwen3-coder:kai"
# Fast grounded-lookup accelerator for Juris Kai simple queries. 1.0GB, served
# by the same VM104 ollama. Kept in the registry so the fabric inventory,
# health surface and Command Center see it as a first-class local model; the
# Juris streaming path routes to it by name (see core/juris_kai/routing.py).
_SMALL_MODEL = os.environ.get("JURIS_KAI_SMALL_MODEL", "qwen2.5:1.5b")
_OLLAMA_BASE_URL = "http://localhost:11434"
# Keep the P40 brain resident between sparse calls. Without an explicit
# keep_alive, ollama unloads the ~18.5GB model after its default 5-min idle,
# so the next request pays a ~50s cold load -- which the router records as
# provider latency and wrongly demotes the GPU provider for.
_OLLAMA_KEEP_ALIVE = os.environ.get("KAI_OLLAMA_KEEP_ALIVE", "30m")
_CPU_BASE_URL = os.environ.get("KAI_CPU_BASE_URL", "http://192.168.1.242:5001")
_CPU_JUMP_HOST = os.environ.get("KAI_CPU_JUMP_HOST", "root@100.122.38.118")


def _ollama_model_present(model=_KAI_MODEL, timeout=2):
    """True only when ollama (:11434) is up AND currently serves ``model``."""
    try:
        import requests
        r = requests.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=timeout)
        if r.status_code != 200:
            return False
        return any(
            model in (m.get("name"), m.get("model"))
            for m in r.json().get("models", [])
        )
    except Exception:
        return False


def _kai_ollama_run_text_task(prompt, timeout=240, project_path=None, model=_KAI_MODEL):
    """Shared chat call for the VM104 ollama fabric models."""
    import requests
    try:
        r = requests.post(
            f"{_OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": _OLLAMA_KEEP_ALIVE,
                "options": {"temperature": 0.7, "top_p": 0.9},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception as e:
        raise RuntimeError(f"local ollama ({model}) call failed: {e}")


def _kai_brain_run_text_task(prompt, timeout=240, project_path=None):
    return _kai_ollama_run_text_task(prompt, timeout=timeout)


def _kai_coder_run_text_task(prompt, timeout=120, project_path=None):
    return _kai_ollama_run_text_task(prompt, timeout=timeout)


def _kai_deep_run_text_task(prompt, timeout=240, project_path=None):
    return _kai_ollama_run_text_task(prompt, timeout=timeout)


def _kai_small_run_text_task(prompt, timeout=120, project_path=None):
    """qwen2.5:1.5b via the VM104 ollama fabric — fast grounded lookups.

    Text-only: the small model accelerates wording of an already-grounded
    prompt, it is never a coding agent.
    """
    return _kai_ollama_run_text_task(prompt, timeout=timeout,
                                     model=_SMALL_MODEL)


def _kai_small_available():
    """True only when ollama serves the small model (qwen2.5:1.5b)."""
    return _ollama_model_present(_SMALL_MODEL)


def _kai_brain_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model=_KAI_MODEL, timeout=timeout
    )


def _kai_coder_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model=_KAI_MODEL, timeout=timeout
    )


def _llama_cpu_health(timeout=3):
    """Reachability probe for the VM112 llama.cpp server (:5001)."""
    try:
        import requests
        r = requests.get(f"{_CPU_BASE_URL}/health", timeout=timeout)
        return r.status_code == 200 and "ok" in r.text
    except Exception:
        return _llama_cpu_health_jump()


def _llama_cpu_health_jump():
    """Passive SSH-jump probe for runners without direct VM112 LAN reach."""
    import subprocess
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "-J", _CPU_JUMP_HOST, "kai@192.168.1.242",
             "curl", "-s", "-m", "5", "http://localhost:5001/health"],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def _llama_cpu_run_text_task(prompt, timeout=300, project_path=None):
    import requests
    try:
        r = requests.post(
            f"{_CPU_BASE_URL}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0.1,
                "top_p": 0.95,
                "stream": False,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"llama_coder_cpu ({_CPU_BASE_URL}) call failed: {e}")


def _llama_cpu_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="llama_coder_cpu", timeout=timeout
    )


# ── Proxmox-B-era local text provider (retained, now honest) ────────────────
# `local` predates the VM104 fabric reorg. It is still a local provider, but
# its original model (qwen2.5:7b) is no longer served, so its availability is
# gated on the model actually being present rather than merely the ollama
# endpoint answering.
def _local_run_text_task(prompt, timeout=120, project_path=None):
    """qwen3-coder:kai via the VM104 ollama fabric.

    Historically pointed at qwen2.5:7b on Proxmox B; that model is no longer
    served, so every call raised HTTPError and any route that fell through to
    `local` (e.g. the Kai-bot planning chain) broke. Aliased to the same VM104
    ollama call as kai_brain — the model /api/tags actually reports — so
    `local` is honest about what it serves.
    """
    return _kai_ollama_run_text_task(prompt, timeout=timeout)


def _local_available():
    """True only when ollama serves the model `local` targets."""
    return _ollama_model_present(_KAI_MODEL)


register_provider(
    "kai_brain",
    run_coding_task=_kai_brain_run_coding_task,
    run_text_task=_kai_brain_run_text_task,
    available_fn=lambda: _ollama_model_present(_KAI_MODEL),
    kind="local",
    description="qwen3-coder:kai (Qwen3-MoE 30B) via ollama on VM104 P40 — kai.brain: primary brain + orchestrator; writes code itself via local_coding_bridge when not reviewing.",
    cost_tier="free",
)

register_provider(
    "kai_coder",
    run_coding_task=_kai_coder_run_coding_task,
    run_text_task=_kai_coder_run_text_task,
    available_fn=lambda: _ollama_model_present(_KAI_MODEL),
    kind="local",
    description="qwen3-coder:kai (Qwen3-MoE 30B) via ollama on VM104 P40 — kai.coder: coding specialist. Code generation, review, refactoring, bug detection, technical documentation.",
    cost_tier="free",
)

register_provider(
    "kai_deep",
    run_text_task=_kai_deep_run_text_task,
    available_fn=lambda: _ollama_model_present(_KAI_MODEL),
    kind="local",
    description="qwen3-coder:kai via ollama on VM104 P40 — kai.deep: deep-reasoning escalation for difficult architecture/reasoning/investigation tasks.",
    cost_tier="free",
)

register_provider(
    "kai_small",
    run_text_task=_kai_small_run_text_task,
    available_fn=_kai_small_available,
    kind="local",
    description="qwen2.5:1.5b via ollama on VM104 P40 — kai.small: fast grounded-lookup accelerator for simple Juris Kai questions; text-only, availability-gated on the model actually being served.",
    cost_tier="free",
)

register_provider(
    "llama_coder_cpu",
    run_coding_task=_llama_cpu_run_coding_task,
    run_text_task=_llama_cpu_run_text_task,
    available_fn=_llama_cpu_health,
    kind="local",
    description="Qwen2.5-Coder-7B Q4_K_M via llama.cpp server on VM112 (192.168.1.242:5001) — independent CPU-only node; the VM104-loss failover for every core role.",
    cost_tier="free",
)


register_provider(
    "local",
    run_text_task=_local_run_text_task,
    available_fn=_local_available,
    kind="local",
    description="qwen3-coder:kai via ollama on VM104 P40 — local text-task provider (aliased to the served VM104 model; formerly qwen2.5:7b).",
    cost_tier="free",
)
