"""KAI 2.0 Phase 2 — Command Center aggregation routes.

Adds the Model Fabric catalog (per-model pages), a live model test-call, a
fabric summary (health/routing/utilization) and a security overview. These
are **read-only projections** over existing sources of truth:

* ``core.ai_provider.list_providers`` — provider registry
* ``core.model_registry.build_registry`` — per-model projection (caps, roles)
* ``core.ai.ai_router.get_provider_dashboard`` / ``ROLE_PROVIDERS`` — health/routing
* ``core.telemetry.snapshot`` — usage (calls, EMA latency)
* Ollama ``/api/tags`` + ``/api/ps`` (VM104 P40) — params, context, VRAM
* llama.cpp ``/health`` + ``/v1/models`` (VM112 CPU) — independent capacity
* ``core.ai.gpu_arbiter`` — in-flight GPU permits
* vault / agentguard / authz — security posture

Mounted by ``core/api.py``. Never raises on a missing source; degrades to
``None``/empty so the Command Center always renders.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

cc_phase2_router = APIRouter(tags=["kai-2.0-command-center"])

OLLAMA_URL = "http://127.0.0.1:11434"
_HTTP_TIMEOUT = 1.5
_CATALOG_TTL = 60.0
_CATALOG_CACHE: dict = {"data": None, "at": 0.0}
_CATALOG_LOCK = threading.Lock()
_CATALOG_REFRESHING = False

# Static fallback specs for local artefacts where a live probe is unavailable.
MODEL_SPECS = {
    "qwen3-coder:kai": {"params": "30.5B MoE", "context_length": 262144,
                        "quantization": "Q4_K_M", "size_gb": 18.6, "family": "qwen3moe"},
    "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf": {
        "params": "7B", "context_length": 32768, "quantization": "Q4_K_M",
        "size_gb": 4.4, "family": "qwen2"},
    "llama3.2:3b": {"params": "3B", "context_length": 131072,
                    "quantization": "Q4_K_M", "size_gb": 2.0, "family": "llama"},
}

_TITLE_OVERRIDES = {
    "kai_brain": "Kai Brain",
    "kai_coder": "Kai Coder",
    "kai_deep": "Kai Deep",
    "llama_coder_cpu": "Llama Coder (CPU)",
    "koboldcpp_cpu_a": "KoboldCPP CPU (alias)",
    "local_brain_fast": "Local Brain Fast",
    "local_coder": "Local Coder",
}


# ── low-level helpers ────────────────────────────────────────────────────────
def _get_json(url: str, timeout: float = _HTTP_TIMEOUT) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _ollama_snapshot() -> dict:
    """artifact-name -> {params, quantization, context_length, loaded, vram_gb}."""
    out: dict[str, dict] = {}
    tags = _get_json(f"{OLLAMA_URL}/api/tags") or {}
    for m in tags.get("models", []) or []:
        name = m.get("name") or m.get("model")
        if not name:
            continue
        d = m.get("details") or {}
        out[name] = {
            "params": d.get("parameter_size"),
            "quantization": d.get("quantization_level"),
            "context_length": d.get("context_length"),
            "size_gb": round((m.get("size") or 0) / 1e9, 2) or None,
            "family": d.get("family"),
            "loaded": False,
            "vram_gb": None,
            "loaded_context": None,
        }
    ps = _get_json(f"{OLLAMA_URL}/api/ps") or {}
    for m in ps.get("models", []) or []:
        name = m.get("name") or m.get("model")
        if not name:
            continue
        rec = out.setdefault(name, {})
        rec["loaded"] = True
        rec["vram_gb"] = round((m.get("size_vram") or 0) / 1e9, 2) or None
        rec["loaded_context"] = m.get("context_length")
    return out


def _llama_snapshot(endpoints: dict) -> dict:
    """Probe llama.cpp CPU servers (VM112) health + model id."""
    out: dict[str, dict] = {}
    for name, rec in (endpoints or {}).items():
        if not rec or "5001" not in str(rec):
            continue
        health = _get_json(f"{rec.rstrip('/')}/health")
        if health is None:
            out[name] = {"reachable": False, "endpoint": rec}
            continue
        models = _get_json(f"{rec.rstrip('/')}/v1/models") or {}
        ids = [m.get("id") for m in (models.get("data") or []) if m.get("id")]
        out[name] = {"reachable": True, "endpoint": rec,
                     "models": ids or [models.get("name")]}
    return out


def _title(model_id: str) -> str:
    if model_id in _TITLE_OVERRIDES:
        return _TITLE_OVERRIDES[model_id]
    pretty = model_id.replace("_", " ").replace("-", " ").strip()
    return " ".join(w.capitalize() for w in pretty.split()) or model_id


def _task_types_for(model_id: str, role_providers: dict) -> list:
    return sorted(role for role, chain in (role_providers or {}).items()
                  if model_id in (chain or []))


# ── pure aggregation (unit-testable, all sources injectable) ─────────────────
def build_catalog(providers: dict, registry: dict, dashboard: dict,
                  weights: dict, telemetry: dict, ollama: dict,
                  llama: dict, role_providers: dict,
                  endpoints: dict) -> dict:
    models = []
    reg_models = (registry or {}).get("models", {}) or {}
    tel_providers = ((telemetry or {}).get("model", {}) or {}).get("providers", {}) or {}

    for name in sorted(providers):
        entry = providers.get(name, {}) or {}
        reg = reg_models.get(name, {}) or {}
        dash = dashboard.get(name, {}) or {}
        artifact = reg.get("model")
        spec = MODEL_SPECS.get(artifact, {}) if artifact else {}
        live = (ollama or {}).get(artifact, {}) if artifact else {}
        llama_rec = (llama or {}).get(name, {}) if not artifact or name not in ollama else {}

        attempt = dash.get("total_attempts")
        succ = dash.get("total_successes")
        usage = {
            "attempts": attempt,
            "successes": succ,
            "success_rate": dash.get("success_rate"),
            "avg_duration_ms": dash.get("average_duration_ms"),
            "last_response_time_ms": dash.get("last_response_time_ms"),
            "last_request_at": dash.get("last_request_at"),
            "total_cost": dash.get("total_cost"),
            "cost_reported_calls": dash.get("cost_reported_calls"),
            "telemetry_calls": (tel_providers.get(name) or {}).get("count"),
            "ema_ms": (tel_providers.get(name) or {}).get("ema_ms"),
        }
        caps = sorted(set(reg.get("capabilities") or entry.get("capabilities") or []))
        models.append({
            "id": name,
            "title": _title(name),
            "provider": name,
            "kind": entry.get("kind") or reg.get("kind") or "unknown",
            "model": artifact,
            "description": entry.get("description") or reg.get("description") or "",
            "params": live.get("params") or spec.get("params"),
            "quantization": live.get("quantization") or spec.get("quantization"),
            "size_gb": live.get("size_gb") or spec.get("size_gb"),
            "context_length": live.get("context_length") or spec.get("context_length"),
            "context_loaded": live.get("loaded_context"),
            "capabilities": caps,
            "roles": reg.get("roles") or [],
            "task_types": _task_types_for(name, role_providers),
            "endpoint": reg.get("endpoint") or endpoints.get(name),
            "cost_tier": entry.get("cost_tier") or reg.get("cost_tier") or "unknown",
            "available": bool(entry.get("available", reg.get("available", False))),
            "enabled": bool(entry.get("enabled", reg.get("enabled", True))),
            "health": dash.get("health"),
            "status": dash.get("status"),
            "quota_status": dash.get("percent_remaining"),
            "weight": (weights or {}).get(name),
            "gpu": {
                "loaded": bool(live.get("loaded")),
                "vram_gb": live.get("vram_gb"),
                "device": "P40 (VM104)" if live.get("loaded") or live.get("vram_gb") else (
                    "CPU (VM112)" if llama_rec.get("reachable") else None),
                "llama_reachable": llama_rec.get("reachable"),
            },
            "usage": usage,
            "benchmark": reg.get("benchmark"),
            "testable": True,
        })

    return {
        "schema": 1,
        "generated_at": time.time(),
        "counts": {
            "models": len(models),
            "available": sum(1 for m in models if m["available"]),
            "enabled": sum(1 for m in models if m["enabled"]),
            "loaded": sum(1 for m in models if m["gpu"]["loaded"]),
        },
        "models": models,
    }


def collect_catalog(force: bool = False) -> dict:
    """Return the per-model catalog, refreshing in the background when stale.

    ``list_providers`` + ``get_provider_dashboard`` each probe every provider
    (~6s each), so a synchronous rebuild blocks the request for >12s. We keep a
    snapshot, serve it immediately, and refresh in a daemon thread. The first
    ever call (cold cache) may block; the daemon pre-warm below makes that rare.
    """
    now = time.time()
    with _CATALOG_LOCK:
        data = _CATALOG_CACHE["data"]
        age = now - _CATALOG_CACHE["at"]
    if data is not None:
        if force or age > _CATALOG_TTL:
            _start_catalog_refresh()
        return data
    # Cold cache: build synchronously (only on the very first request).
    return _build_and_cache()


def _build_and_cache() -> dict:
    # If a background refresh is already running (e.g. the import pre-warm),
    # wait for it instead of duplicating the ~12s provider probe.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        with _CATALOG_LOCK:
            if _CATALOG_CACHE["data"] is not None:
                return _CATALOG_CACHE["data"]
            refreshing = _CATALOG_REFRESHING
        if not refreshing:
            break
        time.sleep(0.2)
    with _CATALOG_LOCK:
        if _CATALOG_CACHE["data"] is not None:
            return _CATALOG_CACHE["data"]
        data = _collect_catalog_uncached()
        _CATALOG_CACHE["data"] = data
        _CATALOG_CACHE["at"] = time.time()
        return data


def _start_catalog_refresh() -> None:
    global _CATALOG_REFRESHING
    with _CATALOG_LOCK:
        if _CATALOG_REFRESHING:
            return
        _CATALOG_REFRESHING = True

    def _run():
        global _CATALOG_REFRESHING
        try:
            data = _collect_catalog_uncached()
            with _CATALOG_LOCK:
                _CATALOG_CACHE["data"] = data
                _CATALOG_CACHE["at"] = time.time()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _CATALOG_LOCK:
                _CATALOG_REFRESHING = False

    threading.Thread(target=_run, daemon=True).start()


def _collect_catalog_uncached() -> dict:
    from core.ai_provider import list_providers
    from core.model_registry import build_registry, _ENDPOINTS
    from core.ai.ai_router import get_provider_dashboard, ROLE_PROVIDERS
    from core.telemetry import snapshot as telemetry_snapshot

    try:
        from core.weighted_routing import get_weighted_routing_report
        weights = (get_weighted_routing_report() or {}).get("weights", {})
    except Exception:  # noqa: BLE001
        weights = {}

    providers = list_providers()
    registry = build_registry(providers=providers, role_providers=ROLE_PROVIDERS)
    try:
        dashboard = get_provider_dashboard()
    except Exception:  # noqa: BLE001
        dashboard = {}
    try:
        telemetry = telemetry_snapshot()
    except Exception:  # noqa: BLE001
        telemetry = {}
    ollama = _ollama_snapshot()
    llama = _llama_snapshot(_ENDPOINTS)
    return build_catalog(providers, registry, dashboard, weights, telemetry,
                         ollama, llama, ROLE_PROVIDERS, _ENDPOINTS)


# ── endpoints ────────────────────────────────────────────────────────────────
@cc_phase2_router.get("/api/models/catalog")
def models_catalog(refresh: int = 0):
    """Per-model catalog backing the Model Fabric index + per-model pages."""
    return collect_catalog(force=bool(refresh))


@cc_phase2_router.get("/api/fabric/summary")
def fabric_summary():
    """Models / providers / health / routing / utilization in one call."""
    catalog = collect_catalog()
    from core.ai.ai_router import ROLE_PROVIDERS
    from core import provider_config_editor
    try:
        overrides = provider_config_editor.load_overrides().get("overrides", {})
    except Exception:  # noqa: BLE001
        overrides = {}
    chains = {}
    default_chains = {}
    for module, chain in ROLE_PROVIDERS.items():
        default_chains[module] = list(chain or [])
        chains[module] = list((overrides.get("fallback_order", {}) or {}).get(module, chain or []))

    arbiter = {}
    try:
        from core.ai.gpu_arbiter import gpu_arbiter
        arbiter = gpu_arbiter.stats()
    except Exception:  # noqa: BLE001
        arbiter = {}

    models = catalog["models"]
    by_health = {}
    for m in models:
        h = (m.get("health") or "unknown")
        by_health[h] = by_health.get(h, 0) + 1

    # §7 local diversity: per-provider health/node + per-role failover verdict
    # derived from the already-collected catalog (no extra provider probes).
    from core.model_registry import node_for
    provider_state = {}
    for m in models:
        provider_state[m["id"]] = {
            "kind": m.get("kind"),
            "available": bool(m.get("available")),
            "enabled": bool(m.get("enabled")),
            "cost_tier": m.get("cost_tier"),
            "endpoint": m.get("endpoint"),
            "node": node_for(m["id"]),
            "health": m.get("health"),
        }
    diversity = {}
    for role, chain in chains.items():
        nodes = {node_for(p) for p in chain if p in provider_state}
        avail_nodes = {
            node_for(p) for p in chain
            if p in provider_state and provider_state[p]["available"]
            and provider_state[p]["enabled"]
        }
        diversity[role] = {
            "providers": len(chain),
            "nodes": len(nodes),
            "available_nodes": len(avail_nodes),
            "has_failover": len(nodes) >= 2,
        }
    return {
        "schema": 1,
        "generated_at": catalog["generated_at"],
        "counts": catalog["counts"],
        "by_health": by_health,
        "routing": {"chains": chains, "default_chains": default_chains,
                    "overrides": overrides,
                    "provider_state": provider_state,
                    "diversity": diversity},
        "utilization": {"gpu_arbiter": arbiter,
                        "loaded_models": [{"id": m["id"], "model": m["model"],
                                           "vram_gb": m["gpu"]["vram_gb"],
                                           "context": m["context_loaded"]}
                                          for m in models if m["gpu"]["loaded"]]},
    }


def _require_model_operator(
        x_kai_session: str | None = Header(default=None),
        authorization: str | None = Header(default=None)) -> str:
    """Accept a bridge token or a session with delegate.use.

    Returns a safe label — never the raw session token.
    """
    import hmac
    from core import authz
    from core.bridge_auth import BRIDGE_OPERATOR, _load_api_token

    if authorization and hmac.compare_digest(
            authorization.encode(), f"Bearer {_load_api_token()}".encode()):
        return BRIDGE_OPERATOR
    if x_kai_session and authz.check_capability(x_kai_session, "delegate.use"):
        return "operator"
    raise HTTPException(status_code=401, detail="Missing or invalid credentials")


class ModelTestBody(BaseModel):
    prompt: Optional[str] = None


@cc_phase2_router.post("/api/models/{model_id}/test")
def model_test_call(model_id: str, body: ModelTestBody = ModelTestBody(),
                    operator: str = Depends(_require_model_operator)):
    """Route a tiny live prompt through the fabric to one model."""
    from core.ai.ai_router import delegate
    prompt = (body.prompt or "Reply with the single word: pong").strip()[:500]
    started = time.time()
    try:
        result = delegate(prompt, task_type="classification", provider=model_id,
                          capability="text_task", timeout=45)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "model": model_id, "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.time() - started) * 1000)}
    latency = round((time.time() - started) * 1000)
    text = result if isinstance(result, str) else str(result)
    return {"ok": True, "model": model_id, "operator": operator,
            "latency_ms": latency, "response": text[:500]}


@cc_phase2_router.get("/api/security/overview")
def security_overview():
    """AgentGuard + permissions + policy + vault + recent events."""
    from core import authz

    roles = {role: sorted(caps or []) for role, caps in
             (getattr(authz, "ROLE_CAPABILITIES", {}) or {}).items()}
    accounts = []
    try:
        for username, rec in (authz._read_accounts() or {}).items():
            rec = rec or {}
            accounts.append({"username": username, "role": rec.get("role"),
                             "active": rec.get("active", True),
                             "created_at": rec.get("created_at")})
    except Exception:  # noqa: BLE001
        accounts = []

    findings = []
    try:
        from core.kai.architecture_guardian import ArchitectureGuardian
        from core.cc_extra_routes import _KNOWN_SERVICES, _svc_active
        inv = [{"name": n, "port": p, "enabled": True, "active": _svc_active(s)}
               for n, p, s in _KNOWN_SERVICES]
        findings = [f.to_dict() for f in ArchitectureGuardian().scan(inv)]
    except Exception:  # noqa: BLE001
        findings = []

    vault = {"capabilities": 0, "audit_events": 0, "subjects": []}
    try:
        from core.vault.api import _BROKER, _default_policy
        vault = {"capabilities": len(_BROKER.list()),
                 "audit_events": len(_BROKER.audit(1000)),
                 "subjects": sorted((_default_policy() or {}).keys())}
    except Exception:  # noqa: BLE001
        pass

    approvals = {"pending": 0, "items": []}
    try:
        from core.memory import load as mem_load
        pending = mem_load("approval_queue") or []
        if isinstance(pending, dict):
            pending = pending.get("requests", [])
        approvals = {"pending": len(pending or []),
                     "items": (pending or [])[:20]}
    except Exception:  # noqa: BLE001
        pass

    events = []
    try:
        from core.self_healing import run_self_healing
        events = (run_self_healing() or [])[-30:]
    except Exception:  # noqa: BLE001
        events = []

    return {
        "schema": 1,
        "generated_at": time.time(),
        "agentguard": {"enabled": True, "mode": "enforce",
                       "findings": findings},
        "permissions": {"roles": roles, "accounts": accounts},
        "policy": {"approval_policy": _approval_policy(),
                   "pending_approvals": approvals["pending"],
                   "pending": approvals["items"]},
        "vault": vault,
        "events": events,
    }


def _approval_policy() -> str:
    try:
        from core.approval_policy import policy
        return policy()
    except Exception:  # noqa: BLE001
        return "scoped"


def _probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:  # noqa: BLE001
        return False


# Pre-warm the model catalog at import (background) so the Command Center's
# Model Fabric / per-model pages have data ready without a 12s first request.
# Skipped under pytest so tests never probe live providers.
import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
if "pytest" not in _sys.modules and not _os.environ.get("PYTEST_CURRENT_TEST"):
    try:
        _start_catalog_refresh()
    except Exception:  # noqa: BLE001
        pass
