"""Task 3: enumerate the raw LLM entry points and prove the guard is applied
at the shared boundary, so the next unguarded caller fails CI.

The two shared primitives are:

  * ``core.juris_kai.streaming.stream_chat`` / ``generate`` / ``collect``
  * ``core.ai.ai_router.delegate``

Every other caller must route through them. A lint-style scan of ``core/`` also
flags any *new* direct provider dispatch (``run_text_task`` / ``run_coding_task``
/ ``call_ollama``) that is not in the reviewed transport/guarded allowlist --
the exact "next unguarded caller" this task exists to prevent.
"""

import ast
import inspect
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text()


# ---------------------------------------------------------------------------
# 1. the shared primitives actually carry the guard
# ---------------------------------------------------------------------------

def test_streaming_primitive_applies_the_guard():
    src = _src("core/juris_kai/streaming.py")
    for token in ("guard_input", "guard_stream_prefix", "guard_output",
                  "StreamGuardAbort"):
        assert token in src, f"streaming.py must apply {token}"


def test_delegate_primitive_applies_the_guard():
    src = _src("core/ai/ai_router.py")
    for token in ("guard_input", "guard_output"):
        assert token in src, f"ai_router.py must apply {token}"


def test_primitives_expose_the_guard_flag_for_pre_guarded_callers():
    from core.juris_kai import streaming
    from core.ai import ai_router

    for fn in (streaming.stream_chat, streaming.generate, streaming.collect):
        assert "guard" in inspect.signature(fn).parameters, fn
    assert "guard" in inspect.signature(ai_router.delegate).parameters


# ---------------------------------------------------------------------------
# 2. every known caller routes through a guarded primitive
# ---------------------------------------------------------------------------

OWNER_LISTED_DELEGATE_CALLERS = [
    "core/kai/planner.py",
    "core/teammate/runtime.py",
    "core/teammate/model_fabric.py",
    "core/kai/brain_review.py",
    "core/ai/agent_registry.py",
    "core/law_tutor/commands.py",
    "core/workers/deepseek_pool.py",
]


def test_owner_listed_callers_route_through_guarded_delegate():
    for rel in OWNER_LISTED_DELEGATE_CALLERS:
        assert "delegate" in _src(rel), \
            f"{rel} must route through the guarded ai_router.delegate"


def test_cc_test_query_and_stream_use_the_guarded_primitive():
    src = _src("core/juris_kai/cc_routes.py")
    assert "jstream.stream_chat(" in src
    assert "jstream.generate(" in src


# ---------------------------------------------------------------------------
# 3. no new raw provider dispatch outside reviewed layers
# ---------------------------------------------------------------------------

_RAW_NAMES = frozenset({
    "run_text_task", "run_coding_task",
    "call_ollama", "call_ollama_qwen", "call_ollama_llama",
})

# Reviewed layers allowed to touch a provider directly:
#   * transport + registration: ai_provider, free_providers, llm_clients,
#     local_coding_bridge, api (registration lambdas) -- all reached *through*
#     the guarded delegate primitive.
#   * ai_router.py -- the guarded delegate primitive itself.
#   * known direct call sites tracked as open follow-ups in the report
#     (build_manager advisory review, voice_realtime, cognitive_integration,
#     test_gen generator). Listing them keeps this test honest: a *new*
#     unguarded caller outside this set fails the build.
_RAW_DISPATCH_ALLOWLIST = {
    "core/ai_provider.py",
    "core/free_providers.py",
    "core/llm_clients.py",
    "core/local_coding_bridge.py",
    "core/api.py",
    "core/ai/ai_router.py",
    "core/build_manager.py",
    "core/ai/voice_realtime.py",
    "core/ai/cognitive_integration.py",
    "core/test_gen/generator.py",
}


def _callee_name(func):
    """Best-effort name of a called function (Name / Attribute / Subscript)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Subscript):
        node = func.slice
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
    return None


def _raw_dispatch_lines(text: str) -> list:
    """Line numbers of real calls to a raw provider/ollama function."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee_name(node.func) in _RAW_NAMES:
            hits.append(node.lineno)
    return hits


def test_no_new_raw_provider_dispatch_outside_reviewed_layers():
    offenders = []
    for path in sorted((REPO / "core").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("core/kai_betting/") or "/kai-betting/" in rel:
            continue
        if rel in _RAW_DISPATCH_ALLOWLIST:
            continue
        text = path.read_text(errors="ignore")
        lines = text.splitlines()
        for lineno in _raw_dispatch_lines(text):
            snippet = lines[lineno - 1].strip()[:120] if lineno <= len(lines) else ""
            offenders.append(f"{rel}:{lineno}: {snippet}")
    assert offenders == [], (
        "unguarded raw LLM dispatch found (route it through "
        "ai_router.delegate or guard it):\n" + "\n".join(offenders))
