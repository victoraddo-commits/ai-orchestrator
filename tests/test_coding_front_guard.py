"""Regression guard (2026-09-13): the coding rotating front must NEVER
contain a slow CPU provider.

Root cause it locks in: CODING_ROTATING_FRONT had been changed to include
llama_coder_cpu (VM 112, ~9x slower than the P40), so ~half of coding
generations started on the CPU model and blew the build timeout (live: build
21I generated on llama_coder_cpu while 20C used kai_coder for the same phase
shape). The front is now derived by filtering SLOW_CODING_PROVIDERS.
"""
from core.ai import ai_router


def test_slow_providers_are_defined():
    assert ai_router.SLOW_CODING_PROVIDERS
    assert "llama_coder_cpu" in ai_router.SLOW_CODING_PROVIDERS


def test_rotating_front_excludes_slow_cpu_providers():
    assert ai_router.SLOW_CODING_PROVIDERS.isdisjoint(
        ai_router.CODING_ROTATING_FRONT)


def test_coding_tries_gpu_first_across_rotations(monkeypatch):
    monkeypatch.setattr(
        ai_router, "get_effective_providers",
        lambda tt: ["kai_coder", "llama_coder_cpu", "local"])
    for _ in range(10):
        assert ai_router._candidates_for("coding")[0] == "kai_coder"
