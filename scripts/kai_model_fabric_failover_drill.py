#!/usr/bin/env python
"""§7 Model Fabric — live local-node failover drill (2026-09-18).

Proves on the real fabric (no cloud, no mocks of the successful leg) that a
task completes on a *different local model* when the primary local node
(VM104 GPU, ollama localhost:11434) is unavailable or times out.

Failure injection is in-process only (``available_fn`` / ``run_text_task`` on
the VM104 provider entries); the failover target — ``llama_coder_cpu`` on
VM112 (llama.cpp, 192.168.1.242:5001) — performs real inference.

Run:  .venv/bin/python scripts/kai_model_fabric_failover_drill.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.ai.ai_router as ai_router
import core.ai_provider as ai_provider
import core.ai.circuit_breaker as circuit_breaker
from core.logger import info

VM104 = ["kai_brain", "kai_coder", "kai_deep", "local"]
VM112 = ["llama_coder_cpu"]

_SAVED: dict = {}


def _snapshot(names):
    for name in names:
        prov = ai_provider.get_provider(name)
        if prov is not None and name not in _SAVED:
            _SAVED[name] = dict(prov)


def _available(name, value):
    _snapshot([name])
    ai_provider.get_provider(name)["available_fn"] = lambda: value


def _run_text(name, fn):
    _snapshot([name])
    ai_provider.get_provider(name)["run_text_task"] = fn


def _restore():
    for name, snap in _SAVED.items():
        prov = ai_provider.get_provider(name)
        if prov is not None:
            prov.update(snap)
    _SAVED.clear()


def main() -> int:
    report = []
    circuit_breaker.reset_all_breakers()

    # ── topology (no injection) ────────────────────────────────────────────
    chains = ai_router.provider_chain_report()
    report.append({
        "step": "topology",
        "planning_chain": chains["chains"]["planning"],
        "coding_chain": chains["chains"]["coding"],
        "planning_diversity": chains["diversity"]["planning"],
        "coding_diversity": chains["diversity"]["coding"],
        "vm112_node": chains["provider_state"]["llama_coder_cpu"]["node"],
    })

    # ── scenario A: VM104 unavailable -> planning completes on VM112 ───────
    _snapshot(VM104)
    for name in VM104:
        _available(name, False)
    circuit_breaker.reset_all_breakers()
    res_a = ai_router.delegate(
        "In one sentence, what is a Python decorator?",
        task_type="planning", timeout=240, return_attempts=True,
    )
    report.append({
        "step": "A_vm104_unavailable_planning",
        "provider": res_a["provider"],
        "failed_over": res_a["provider"] in VM112,
        "attempts": res_a.get("attempts"),
        "response_head": str(res_a["response"])[:200],
        "duration_ms": res_a["duration_ms"],
    })
    _restore()

    # ── scenario B: VM104 timeout on the coding role -> VM112 answers ──────
    def _timeout(prompt, timeout=60, project_path=None):
        raise TimeoutError("kai_coder request failed: ReadTimeout")

    _available("kai_coder", True)
    _run_text("kai_coder", _timeout)
    circuit_breaker.reset_all_breakers()
    res_b = ai_router.delegate(
        "Write a one-line Python docstring for a function add(a, b).",
        task_type="coding", timeout=240, return_attempts=True,
    )
    report.append({
        "step": "B_vm104_timeout_coding",
        "provider": res_b["provider"],
        "failed_over": res_b["provider"] in VM112,
        "attempts": res_b.get("attempts"),
        "response_head": str(res_b["response"])[:200],
        "duration_ms": res_b["duration_ms"],
    })
    _restore()

    ok = all(
        s.get("failed_over") is True
        for s in report if s["step"].startswith(("A_", "B_"))
    )
    print(json.dumps({"result": "PASS" if ok else "FAIL", "evidence": report},
                     indent=2))
    info(f"model fabric failover drill: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
