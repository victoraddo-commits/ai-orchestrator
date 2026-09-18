"""Tests for the KAI 2.0 Phase 2 Command Center aggregation routes."""
from __future__ import annotations

from core.cc_phase2_routes import (
    MODEL_SPECS,
    _task_types_for,
    _title,
    build_catalog,
    cc_phase2_router,
)


PROVIDERS = {
    "kai_brain": {
        "kind": "local", "available": True, "enabled": True,
        "capabilities": ["coding_agent", "text_task"], "cost_tier": "free",
        "description": "qwen3-coder:kai on VM104 P40",
    },
    "llama_coder_cpu": {
        "kind": "local", "available": True, "enabled": True,
        "capabilities": ["coding_agent"], "cost_tier": "free",
        "description": "Qwen2.5-Coder on VM112",
    },
    "dead_cloud": {
        "kind": "cloud", "available": False, "enabled": True,
        "capabilities": ["text_task"], "cost_tier": "medium",
    },
}

REGISTRY = {
    "models": {
        "kai_brain": {
            "model": "qwen3-coder:kai",
            "capabilities": ["coding", "reasoning"],
            "roles": ["planning", "review"],
            "endpoint": "http://localhost:11434",
        },
        "llama_coder_cpu": {
            "model": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
            "capabilities": ["coding"], "roles": [], "endpoint": "http://192.168.1.242:5001",
        },
        "dead_cloud": {"model": None, "capabilities": [], "roles": [], "endpoint": None},
    }
}

DASHBOARD = {
    "kai_brain": {"health": "healthy", "status": "connected", "total_attempts": 10,
                  "total_successes": 9, "success_rate": 0.9,
                  "average_duration_ms": 1000, "total_cost": None},
    "llama_coder_cpu": {"health": "degraded", "status": "connected", "total_attempts": 4,
                        "total_successes": 2, "success_rate": 0.5,
                        "average_duration_ms": 7000},
}

WEIGHTS = {"kai_brain": 0.925, "llama_coder_cpu": 0.85}
TELEMETRY = {"model": {"providers": {"kai_brain": {"count": 42, "ema_ms": 1200.0}}}}
OLLAMA = {"qwen3-coder:kai": {"params": "30.5B", "quantization": "Q4_K_M",
                              "context_length": 262144, "size_gb": 18.6,
                              "loaded": True, "vram_gb": 21.7, "loaded_context": 8192}}
LLAMA = {"llama_coder_cpu": {"reachable": True, "endpoint": "http://192.168.1.242:5001",
                             "models": ["llama-coder"]}}
ROLES = {"planning": ["kai_brain"], "coding": ["kai_brain", "llama_coder_cpu"]}
ENDPOINTS = {"kai_brain": "http://localhost:11434",
             "llama_coder_cpu": "http://192.168.1.242:5001"}


def _catalog():
    return build_catalog(PROVIDERS, REGISTRY, DASHBOARD, WEIGHTS, TELEMETRY,
                         OLLAMA, LLAMA, ROLES, ENDPOINTS)


def test_counts_and_titles():
    cat = _catalog()
    assert cat["counts"]["models"] == 3
    assert cat["counts"]["available"] == 2
    assert cat["counts"]["loaded"] == 1
    by_id = {m["id"]: m for m in cat["models"]}
    assert by_id["kai_brain"]["title"] == "Kai Brain"
    assert by_id["llama_coder_cpu"]["title"] == "Llama Coder (CPU)"


def test_model_detail_fields():
    m = {x["id"]: x for x in _catalog()["models"]}["kai_brain"]
    assert m["params"] == "30.5B"
    assert m["quantization"] == "Q4_K_M"
    assert m["context_length"] == 262144
    assert m["context_loaded"] == 8192
    assert m["health"] == "healthy"
    assert m["roles"] == ["planning", "review"]
    assert m["task_types"] == ["coding", "planning"]
    assert m["gpu"]["loaded"] is True
    assert m["gpu"]["vram_gb"] == 21.7
    assert m["usage"]["attempts"] == 10
    assert m["usage"]["success_rate"] == 0.9
    assert m["usage"]["telemetry_calls"] == 42
    assert m["weight"] == 0.925


def test_cpu_model_uses_spec_fallback_and_llama():
    m = {x["id"]: x for x in _catalog()["models"]}["llama_coder_cpu"]
    assert m["params"] == MODEL_SPECS["Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"]["params"]
    assert m["gpu"]["llama_reachable"] is True
    assert m["gpu"]["device"] == "CPU (VM112)"


def test_missing_cloud_model_is_still_listed():
    m = {x["id"]: x for x in _catalog()["models"]}["dead_cloud"]
    assert m["available"] is False
    assert m["health"] is None
    assert m["params"] is None
    assert m["testable"] is True


def test_empty_sources_do_not_raise():
    cat = build_catalog({}, {}, {}, {}, {}, {}, {}, {}, {})
    assert cat["models"] == []
    assert cat["counts"]["models"] == 0


def test_task_types_and_title_helpers():
    assert _task_types_for("kai_brain", ROLES) == ["coding", "planning"]
    assert _title("some_new_model") == "Some New Model"


def test_routes_registered():
    paths = {r.path for r in cc_phase2_router.routes}
    assert "/api/models/catalog" in paths
    assert "/api/fabric/summary" in paths
    assert "/api/models/{model_id}/test" in paths
    assert "/api/security/overview" in paths
