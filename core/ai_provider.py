"""AI provider registry.

Phase 12I built this as architecture prep with two placeholder-ish entries
(claude, local). Phase 12J fills in real text-task providers (gemini, groq,
openai) -- plain request/response chat-completion calls, never file access
or tool use, so this still doesn't duplicate CloudCLI's coding engine. Only
Claude has "coding_agent" capability.
"""

import json
import os
from pathlib import Path

# Load .env file to ensure environment variables are available for provider checks
# This is critical for Qwen3 and other providers that depend on environment variables
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
import core.llm_clients as llm_clients


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

# 13W: static cost classification per provider -- assigned at registration,
# never computed. Valid values, per the user's own labeling request:
#   "free"             -- free-tier API keys (gemini, groq)
#   "free_or_low_cost" -- cheap/credit-pool models (minimax, deepseek, Zen's
#                         default routes)
#   "paid"             -- real per-call billing against a paid account
#                         (openai, claude subscription, openrouter) or
#                         materially expensive models (Sonnet/Opus escalation
#                         tiers -- Fable 5 was chosen for the default Zen
#                         route precisely because it's the cheap one).
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



def _local_run_text_task(prompt, timeout=120, project_path=None, cognitive_role=None):
    """Local AI via ollama with cognitive routing — updated 2026-09-09.

    Routes through cognitive router to select appropriate model for task.
    Currently all roles map to qwen2.5:7b until advanced models imported.
    Uses SSH tunnel to GPU server (192.168.1.241) forwarded to localhost:11434.
    Zero-cost inference, Tesla P40 GPU acceleration.

    Args:
        prompt: Task prompt
        timeout: Request timeout
        project_path: Project context (unused)
        cognitive_role: Optional role (reasoning, coding, fast, etc.)
    """
    # Auto-classify if no explicit role
    if not cognitive_role:
        try:
            from core.ai.cognitive_router import classify_task
            cognitive_role = classify_task(prompt)
        except (ImportError, Exception):
            cognitive_role = None

    return llm_clients.call_ollama(prompt, timeout=timeout, cognitive_role=cognitive_role)


def _local_available():
    """True if the ollama server on localhost:11434 (via SSH tunnel) is responding."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _kai_brain_available():
    """Check if kai-brain:latest model is available in ollama."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return any("kai-brain" in m.get("name", "") for m in models)
        return False
    except Exception:
        return False


def _kai_coder_available():
    """Check if kai-coder:7b model is available in ollama."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return any("kai-coder" in m.get("name", "") for m in models)
        return False
    except Exception:
        return False


def _kai_deep_available():
    """Check if kai-brain:27b (Qwen3.6-27B) model is available in ollama."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return any("kai-brain:27b" == m.get("name", "") for m in models)
        return False
    except Exception:
        return False


def _llama_run_text_task(prompt, timeout=120, project_path=None):
    """llama3.2:3b via ollama on Proxmox B — faster, lighter fallback.

    Deployed 2026-08-11 alongside qwen2.5:7b. Better format compliance
    (no markdown fences) but weaker at hallucination resistance and long
    context. Good for classification, quick lookups, low-latency tasks.

    timeout bumped 60→120 (2026-08-12) to match qwen: under Proxmox B CPU
    contention llama drops to ~5.8 t/s, so long responses can exceed 60s.
    """
    return llm_clients.call_ollama_llama(prompt, timeout=timeout)


def _kai_brain_run_text_task(prompt, timeout=240, project_path=None):
    """kai-brain:latest (GLM-4.7-Flash) via ollama — Kai Brain.

    KAI MODEL TEAM role: kai.brain — primary brain + orchestrator. Handles
    normal conversation, reasoning, planning, decisions, research, and
    coordination; reviews/verifies ALL significant coder output; and writes
    code itself when no higher-priority orchestration/review task requires it.

    GLM-4.7-Flash is ~4.3× faster than the 27B model (~47 vs ~11 tok/s) with
    cleaner output. High timeout retained for headroom on long generations.
    """
    import requests
    import json

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "kai-brain:latest",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "top_p": 0.9,
                }
            },
            timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")
    except Exception as e:
        raise RuntimeError(f"kai-brain model call failed: {e}")


def _kai_deep_run_text_task(prompt, timeout=240, project_path=None):
    """kai-brain:27b (Qwen3.6-27B) via ollama — Kai Deep.

    KAI MODEL TEAM role: kai.deep — deep-reasoning escalation model. Used for
    unusually difficult architecture, reasoning, investigations, or failures
    that Kai Brain cannot confidently solve. High timeout (240s) for the large
    model's inference time.
    """
    import requests
    import json

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "kai-brain:27b",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.6,
                    "top_p": 0.9,
                }
            },
            timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")
    except Exception as e:
        raise RuntimeError(f"kai-deep (kai-brain:27b) model call failed: {e}")


def _kai_coder_run_text_task(prompt, timeout=120, project_path=None):
    """kai-coder:7b via ollama — 4.7GB specialist for code tasks.

    Deployed 2026-09-10. Code-specialized model optimized for:
    - Code generation and completion
    - Code review and refactoring
    - Bug detection and fixes
    - Technical documentation

    Lower temperature (0.1) for more deterministic code output.
    """
    import requests
    import json

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "kai-coder:7b",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Lower for code
                    "top_p": 0.95,
                }
            },
            timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")
    except Exception as e:
        raise RuntimeError(f"kai-coder model call failed: {e}")


def _kai_coder_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    """kai.coder.fast (Qwen2.5-Coder-7B) as an agentic coding worker.

    The local model is text-only, so this routes through
    local_coding_bridge — a deterministic harness that turns the model's
    fenced file output into real writes + git commits — rather than the
    CloudCLI/Claude path that coding_bridge represents.
    """
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="kai-coder:7b", timeout=timeout
    )


def _kai_brain_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    """kai.brain (GLM-4.7-Flash) as a coding worker — the "manager AND
    worker" rule: Kai Brain writes code itself when it isn't busy reviewing.
    Same local_coding_bridge harness, different model."""
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="kai-brain:latest", timeout=timeout
    )


register_provider(
    "local",
    run_text_task=_local_run_text_task,
    available_fn=_local_available,
    kind="local",
    description="qwen2.5:7b via ollama on localhost:11434 (SSH tunnel to GPU server) — PRIMARY local model for all KAI services, zero-cost inference, updated 2026-09-09",
    cost_tier="free",
)

register_provider(
    "llama3",
    run_text_task=_llama_run_text_task,
    available_fn=_local_available,
    kind="local",
    description="llama3.2:3b via ollama on localhost:11434 (fallback model) — faster, better format compliance, good for classification and quick lookups.",
    cost_tier="free",
)


# ============================================================================
# Free Coding Module — verified free OpenRouter models via Free Model Manager
# ============================================================================
# cohere/north-mini-code:free is the first verified free model (7/8 tests,
# coding_score 9.25/10, ACTIVE).  The pool is managed by
# core/free_model_manager/ which discovers, validates, scores, rotates, and
# fails over genuinely free OpenRouter models ($0 prompt + completion).
#
# This provider routes coding tasks to the local Free Model Manager REST API
# (port 8096) which handles failover, circuit-breaking, and pool management.
# The API key and OpenRouter billing stay local — we call our own server.

import requests as _requests


def _free_coding_run_task(project_path, instruction, **kwargs):
    """Route coding task to the Free Model Manager pool.

    The FMM API (port 8096) handles model selection, circuit-breaking, and
    failover.  If the pool is empty or every model is circuit-open it falls
    back to the last-resort error so the router can try its next candidate.
    """
    fmm_url = os.getenv("FREE_CODING_API_URL", "http://localhost:8096")
    timeout = int(os.getenv("FREE_CODING_TIMEOUT", "120"))

    try:
        resp = _requests.post(
            f"{fmm_url}/infer",
            json={"prompt": instruction},
            timeout=timeout,
        )
        if resp.ok:
            data = resp.json()
            if data.get("success"):
                # FMM returns a plain content string; wrap it so the bridge
                # receives the shape coding_bridge expects.
                return {
                    "content": data["content"],
                    "model": data.get("model_id", "unknown"),
                    "latency_ms": data.get("latency_ms", 0),
                }
            else:
                error = data.get("error") or data.get("message") or "Unknown error"
                raise RuntimeError(f"Free coding model error: {error}")
        else:
            raise RuntimeError(f"Free coding API returned {resp.status_code}: {resp.text}")
    except _requests.exceptions.ConnectionError:
        raise RuntimeError("Free Model Manager API is not reachable (is it running on port 8096?)")
    except _requests.exceptions.Timeout:
        raise RuntimeError("Free Model Manager API timed out")


def _free_coding_available():
    """True when the Free Model Manager API is reachable and has at least one
    ACTIVE or AVAILABLE model in the pool."""
    try:
        fmm_url = os.getenv("FREE_CODING_API_URL", "http://localhost:8096")
        resp = _requests.get(f"{fmm_url}/health", timeout=1)
        if not resp.ok:
            return False
        stats = resp.json().get("stats", {})
        return stats.get("active", 0) > 0 or stats.get("verified_free", 0) > 0
    except Exception:
        return False


# Cloud provider removed (100% local only)


# ============================================================================
# LOCAL GPU PROVIDERS (Tesla P40 @ 192.168.1.241)
# KAI 2.0 Model Fabric - Deployed 2026-09-08
# ============================================================================

def _local_text_task(prompt, **kwargs):
    """Local Qwen2.5-7B (BRAIN Fast) - Primary cognitive model.

    Performance: 41 t/s generation, 120 t/s prompt processing
    Hardware: Tesla P40 GPU (23GB VRAM)
    Runtime: llama.cpp server mode via SSH tunnel
    """
    import subprocess
    import json
    import time

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": kwargs.get("max_tokens", 2048),
        "temperature": kwargs.get("temperature", 0.7),
        "stream": False,
    }

    start_time = time.time()
    try:
        # SECURITY FIX 2026-09-10: Use stdin for JSON payload to prevent command injection
        # Replace SSH+curl with direct request (requires Tailscale/direct network access)
        cmd = [
            "ssh", "-J", "root@100.122.38.118",
            "kai@192.168.1.241",
            "curl -s -X POST http://localhost:8001/v1/chat/completions "
            "-H 'Content-Type: application/json' --data-binary @-"
        ]

        result = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH/curl failed: {result.stderr}")

        data = json.loads(result.stdout)
        latency_ms = int((time.time() - start_time) * 1000)
        content = data["choices"][0]["message"]["content"]

        return {
            "content": content,
            "model": "Qwen2.5-7B-Instruct",
            "latency_ms": latency_ms,
            "tokens_prompt": data.get("usage", {}).get("prompt_tokens", 0),
            "tokens_generated": data.get("usage", {}).get("completion_tokens", 0),
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Local BRAIN (Fast) model unavailable: {e}")


def _local_coder_text_task(prompt, **kwargs):
    """Local Qwen2.5-Coder-7B - Code generation specialist.

    Performance: 40.9 t/s generation, 111 t/s prompt processing
    Hardware: Tesla P40 GPU (23GB VRAM)
    Runtime: llama.cpp server mode via SSH tunnel
    """
    import subprocess
    import json
    import time

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": kwargs.get("max_tokens", 2048),
        "temperature": kwargs.get("temperature", 0.1),  # Lower temp for code
        "stream": False,
    }

    start_time = time.time()
    try:
        # SECURITY FIX 2026-09-10: Use stdin for JSON payload to prevent command injection
        cmd = [
            "ssh", "-J", "root@100.122.38.118",
            "kai@192.168.1.241",
            "curl -s -X POST http://localhost:8002/v1/chat/completions "
            "-H 'Content-Type: application/json' --data-binary @-"
        ]

        result = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH/curl failed: {result.stderr}")

        data = json.loads(result.stdout)
        latency_ms = int((time.time() - start_time) * 1000)
        content = data["choices"][0]["message"]["content"]

        return {
            "content": content,
            "model": "Qwen2.5-Coder-7B-Instruct",
            "latency_ms": latency_ms,
            "tokens_prompt": data.get("usage", {}).get("prompt_tokens", 0),
            "tokens_generated": data.get("usage", {}).get("completion_tokens", 0),
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Local CODER model unavailable: {e}")


def _local_brain_available():
    """Check if local BRAIN (Fast) model is reachable via SSH."""
    import subprocess
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-J", "root@100.122.38.118", "kai@192.168.1.241",
             "curl", "-s", "-m", "2", "http://localhost:8001/health"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and "ok" in result.stdout.lower()
    except Exception:
        return False


def _local_coder_available():
    """Check if local CODER model is reachable via SSH."""
    import subprocess
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-J", "root@100.122.38.118", "kai@192.168.1.241",
             "curl", "-s", "-m", "2", "http://localhost:8002/health"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and "ok" in result.stdout.lower()
    except Exception:
        return False


# Register local BRAIN (Fast) - Primary for planning, reasoning, general tasks
register_provider(
    "local_brain_fast",
    run_text_task=_local_text_task,
    available_fn=_local_brain_available,
    kind="local",
    description="Local Qwen2.5-7B (BRAIN Fast) on Tesla P40 GPU — 41 t/s generation, primary for planning/reasoning/analysis. KAI 2.0 Model Fabric.",
    cost_tier="free",
)

# Register local CODER - Primary for code generation/review
register_provider(
    "local_coder",
    run_text_task=_local_coder_text_task,
    available_fn=_local_coder_available,
    kind="local",
    description="Local Qwen2.5-Coder-7B on Tesla P40 GPU — 40.9 t/s generation, specialist for code generation/review/refactoring. KAI 2.0 Model Fabric.",
    cost_tier="free",
)

# Register kai-brain — GLM-4.7-Flash (KAI MODEL TEAM: kai.brain)
register_provider(
    "kai_brain",
    run_coding_task=_kai_brain_run_coding_task,
    run_text_task=_kai_brain_run_text_task,
    available_fn=_kai_brain_available,
    kind="local",
    description="kai-brain:latest (GLM-4.7-Flash) via ollama — KAI MODEL TEAM kai.brain: primary brain + orchestrator. Reviews all coder work and writes code itself when no higher-priority orchestration/review task requires it. ~47 tok/s.",
    cost_tier="free",
)

# Register kai-coder — Qwen2.5-Coder-7B (KAI MODEL TEAM: kai.coder.fast)
register_provider(
    "kai_coder",
    run_coding_task=_kai_coder_run_coding_task,
    run_text_task=_kai_coder_run_text_task,
    available_fn=_kai_coder_available,
    kind="local",
    description="kai-coder:7b (Qwen2.5-Coder-7B) via ollama (4.7GB) — KAI MODEL TEAM kai.coder.fast: fast everyday coding worker. Code generation, review, refactoring, bug detection, technical documentation.",
    cost_tier="free",
)

# Register kai-deep — Qwen3.6-27B (KAI MODEL TEAM: kai.deep)
register_provider(
    "kai_deep",
    run_text_task=_kai_deep_run_text_task,
    available_fn=_kai_deep_available,
    kind="local",
    description="kai-brain:27b (Qwen3.6-27B) via ollama (17GB) — KAI MODEL TEAM kai.deep: deep-reasoning escalation for difficult architecture, reasoning, investigations, or failures Kai Brain cannot confidently solve.",
    cost_tier="free",
)


# ============================================================================
# GPU MODEL FABRIC — dedicated kai-coder:7b replicas on VM 104 P40
# Deployed 2026-09-12 (operator: "i want the cpu models to use the gpu too so
# migrate the models so they can have their own instances"). Two additional
# ollama systemd units (ollama-a on 11435, ollama-b on 11436) on VM 104, each
# with OLLAMA_KEEP_ALIVE=-1 and OLLAMA_MAX_LOADED_MODELS=1 → dedicated instance
# per port. Reachable from this LXC via ssh -L tunnels (ollama-tunnel-a/-b).
# Same kai-coder:7b model as the primary kai_coder, weights shared on GPU
# (ollama dedupes). Purpose: 3-way parallel dispatch across the coder pool.
# ============================================================================

def _ollama_call_port(port: int, model: str, prompt: str, timeout: int = 120,
                      temperature: float = 0.1, top_p: float = 0.95):
    """Hit a specific ollama instance (via SSH tunnel) with a text task.
    Used by the GPU-replica providers so each hits its own dedicated port."""
    import requests
    try:
        response = requests.post(
            f"http://localhost:{port}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "top_p": top_p},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        raise RuntimeError(f"ollama:{port} model {model} call failed: {e}")


def _ollama_available_port(port: int, model_prefix: str) -> bool:
    """Reachability + model-presence probe for a specific ollama port."""
    try:
        import requests
        r = requests.get(f"http://localhost:{port}/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return any(model_prefix in m.get("name", "") for m in models)
        return False
    except Exception:
        return False


# ── kai_coder_gpu_a — dedicated ollama instance on VM 104 GPU port 11435 ──

def _kai_coder_gpu_a_run_text_task(prompt, timeout=120, project_path=None):
    return _ollama_call_port(11435, "kai-coder:7b", prompt, timeout=timeout)


def _kai_coder_gpu_a_available():
    return _ollama_available_port(11435, "kai-coder")


def _kai_coder_gpu_a_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="kai-coder:7b", timeout=timeout,
        ollama_url="http://localhost:11435",
    )


register_provider(
    "kai_coder_gpu_a",
    run_text_task=_kai_coder_gpu_a_run_text_task,
    run_coding_task=_kai_coder_gpu_a_run_coding_task,
    available_fn=_kai_coder_gpu_a_available,
    kind="local",
    description="kai-coder:7b via ollama on VM 104 P40 (instance A, port 11435 via SSH tunnel) — dedicated GPU coder replica for parallel dispatch",
    cost_tier="free",
)


# ── kai_coder_gpu_b — dedicated ollama instance on VM 104 GPU port 11436 ──

def _kai_coder_gpu_b_run_text_task(prompt, timeout=120, project_path=None):
    return _ollama_call_port(11436, "kai-coder:7b", prompt, timeout=timeout)


def _kai_coder_gpu_b_available():
    return _ollama_available_port(11436, "kai-coder")


def _kai_coder_gpu_b_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="kai-coder:7b", timeout=timeout,
        ollama_url="http://localhost:11436",
    )


register_provider(
    "kai_coder_gpu_b",
    run_text_task=_kai_coder_gpu_b_run_text_task,
    run_coding_task=_kai_coder_gpu_b_run_coding_task,
    available_fn=_kai_coder_gpu_b_available,
    kind="local",
    description="kai-coder:7b via ollama on VM 104 P40 (instance B, port 11436 via SSH tunnel) — dedicated GPU coder replica for parallel dispatch",
    cost_tier="free",
)


# ============================================================================
# CPU MODEL FABRIC — KoboldCpp on VM 112 (192.168.1.242) — RETIRED 2026-09-12
# Original deployment 2026-09-11: single instance, GLM-4.7-Flash Q4_K_M on 8080.
# Updated 2026-09-11 (operator directive): replace GLM with two parallel
# Qwen2.5-Coder-7B Q4_K_M instances on ports 5001 + 5002 for concurrent
# coding capacity — same model, two servers, independent request queues.
# 2026-09-12: superseded by kai_coder_gpu_a/_b GPU replicas above. VM 112
# koboldcpp-a/-b services stopped + disabled; .gguf preserved for rollback.
# Registrations below are gated on KAI_KEEP_KOBOLDCPP_CPU=1 for rollback.
# ============================================================================

_KOBOLDCPP_CPU_ENABLED = os.environ.get("KAI_KEEP_KOBOLDCPP_CPU", "").lower() in ("1", "true", "yes")

def _koboldcpp_call(port: int, prompt: str, timeout: int = 300, max_tokens: int = 2048):
    """One HTTP call to a KoboldCpp instance on VM 112 at the given port.

    Routes over SSH -J root@100.122.38.118 → kai@192.168.1.242 → localhost:PORT.
    CPU-only inference on 16 Xeon cores. Higher default timeout (300s)
    because CPU tokens/sec are lower than GPU — expect ~5-15 tok/s.
    """
    import subprocess
    import json
    import time

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    start = time.time()
    try:
        result = subprocess.run(
            [
                "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                "-J", "root@100.122.38.118",
                "kai@192.168.1.242",
                f"curl -s -X POST http://localhost:{port}/v1/chat/completions "
                f"-H 'Content-Type: application/json' --data-binary @-",
            ],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH/curl to KoboldCpp:{port} failed: {result.stderr}")
        data = json.loads(result.stdout)
        return {
            "content": data["choices"][0]["message"]["content"],
            "model": f"Qwen2.5-Coder-7B-Q4_K_M-CPU:{port}",
            "latency_ms": int((time.time() - start) * 1000),
            "tokens_prompt": (data.get("usage") or {}).get("prompt_tokens", 0),
            "tokens_generated": (data.get("usage") or {}).get("completion_tokens", 0),
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
            KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"KoboldCpp:{port} unavailable: {e}")


def _koboldcpp_available_at(port: int) -> bool:
    """Reachability probe for one KoboldCpp instance on VM 112."""
    import subprocess
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "-J", "root@100.122.38.118", "kai@192.168.1.242",
             "curl", "-s", "-m", "5", f"http://localhost:{port}/api/v1/model"],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0 and "koboldcpp" in r.stdout
    except Exception:
        return False


# ── Instance A on port 5001 ───────────────────────────────────────────────

def _koboldcpp_cpu_a_run_text_task(prompt, timeout=300, project_path=None):
    return _koboldcpp_call(5001, prompt, timeout=timeout)


def _koboldcpp_cpu_a_available():
    return _koboldcpp_available_at(5001)


def _koboldcpp_cpu_a_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    from core import local_coding_bridge
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="koboldcpp_cpu_a", timeout=timeout,
    )


if _KOBOLDCPP_CPU_ENABLED:
    register_provider(
        "koboldcpp_cpu_a",
        run_text_task=_koboldcpp_cpu_a_run_text_task,
        run_coding_task=_koboldcpp_cpu_a_run_coding_task,
        available_fn=_koboldcpp_cpu_a_available,
        kind="local",
        description="Qwen2.5-Coder-7B Q4_K_M via KoboldCpp on VM 112 port 5001 — CPU-only, 16 Xeon cores, independent capacity. RETIRED 2026-09-12; set KAI_KEEP_KOBOLDCPP_CPU=1 to re-enable.",
        cost_tier="free",
    )


# ── Instance B on port 5002 (parallel capacity, same model) ───────────────

def _koboldcpp_cpu_b_run_text_task(prompt, timeout=300, project_path=None):
    return _koboldcpp_call(5002, prompt, timeout=timeout)


def _koboldcpp_cpu_b_available():
    return _koboldcpp_available_at(5002)


def _koboldcpp_cpu_b_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    from core import local_coding_bridge
    return local_coding_bridge.run_coding_task(
        project_path, instruction, model="koboldcpp_cpu_b", timeout=timeout,
    )


if _KOBOLDCPP_CPU_ENABLED:
    register_provider(
        "koboldcpp_cpu_b",
        run_text_task=_koboldcpp_cpu_b_run_text_task,
        run_coding_task=_koboldcpp_cpu_b_run_coding_task,
        available_fn=_koboldcpp_cpu_b_available,
        kind="local",
        description="Qwen2.5-Coder-7B Q4_K_M via KoboldCpp on VM 112 port 5002 — CPU-only, parallel to koboldcpp_cpu_a. RETIRED 2026-09-12; set KAI_KEEP_KOBOLDCPP_CPU=1 to re-enable.",
        cost_tier="free",
    )


# Backward-compat alias — anything still importing `koboldcpp_cpu` gets
# routed to instance A. The legacy provider stays registered so old chains
# don't 404; new work should reference _a or _b explicitly, or use the pair
# via the router's rotation.
def _koboldcpp_cpu_run_text_task(prompt, timeout=300, project_path=None):
    return _koboldcpp_cpu_a_run_text_task(prompt, timeout=timeout, project_path=project_path)


def _koboldcpp_cpu_available():
    return _koboldcpp_cpu_a_available()


def _koboldcpp_cpu_run_coding_task(project_path, instruction, timeout=1200, **kwargs):
    return _koboldcpp_cpu_a_run_coding_task(project_path, instruction, timeout=timeout, **kwargs)


if _KOBOLDCPP_CPU_ENABLED:
    register_provider(
        "koboldcpp_cpu",
        run_text_task=_koboldcpp_cpu_run_text_task,
        run_coding_task=_koboldcpp_cpu_run_coding_task,
        available_fn=_koboldcpp_cpu_available,
        kind="local",
        description="Alias for koboldcpp_cpu_a (Qwen2.5-Coder-7B Q4_K_M on VM 112 port 5001). RETIRED 2026-09-12; set KAI_KEEP_KOBOLDCPP_CPU=1 to re-enable.",
        cost_tier="free",
    )
