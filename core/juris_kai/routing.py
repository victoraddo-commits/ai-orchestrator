"""Query-complexity routing: simple lookups -> small local model (Phase E T2).

Quick-mode legal questions split into two very different shapes:

  * **simple / short lookups** ("What is Article 19?", "Define bail") — the
    grounded prompt already carries the retrieved source text, so a small
    resident model can answer in a fraction of the time while giving up little;
  * **hard / analytical asks** ("Explain the fundamental human rights
    provisions", "Compare", "Draft", a multi-clause scenario, or anything in
    Deep Research) — these stay on the 30B ``qwen3-coder:kai``.

The split is decided by a **pure, zero-network-call heuristic** (length,
analytical keywords, multi-clause shape, the explicit ``deep`` flag, and the
task type) — deliberately *not* a second LLM classification call, which would
add latency and cost for no gain. Anything ambiguous defaults to the strong
model: the small model is only chosen for a clearly simple, short query.

Routing never touches grounding. The strict grounding gate runs *before*
generation (``grounding.build_grounded_plan`` refuses ungrounded questions) and
the injection/citation guards run *after* generation on every model's output,
so a routed-small answer is exactly as grounded as a 30B answer — only the
model that words it changes.

**Only downshift when it actually helps.** The shipped default is ``auto``: a
simple query is only sent to the small model when that model is *fully*
GPU-resident alongside the 30B (probed via the local Ollama ``/api/ps`` and
cached). On the current P40 the 30B owns ~21.7 GB of 24 GB, so ``qwen2.5:1.5b``
is half-offloaded to CPU and runs *slower* end-to-end than the resident 30B
(repeated ``scripts/measure_routing.py`` runs: simple lookups 0.4–0.8x the 30B
speed) — so ``auto`` keeps the answer on the 30B instead of shipping a latency
regression. Operators with a card that has free headroom (or that have warmed
and verified the small model) can force ``on``. This is measured, not assumed:
see ``scripts/measure_routing.py``.

Environment overrides (all local, no cloud):

  * ``JURIS_KAI_ROUTING=on``     — force simple queries onto the small model;
  * ``JURIS_KAI_ROUTING=off``    — disable routing entirely (always 30B);
  * ``JURIS_KAI_ROUTING=auto``   — residency-gated (default);
  * ``JURIS_KAI_SMALL_VIABLE=1`` — force the residency gate open (benchmarks);
  * ``JURIS_KAI_FORCE_MODEL``    — pin every answer to one model;
  * ``JURIS_KAI_SMALL_MODEL``    — change the small model (default qwen2.5:1.5b);
  * ``JURIS_KAI_SMALL_FALLBACK=0`` — no 30B retry if the small model fails.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger("juris_kai.routing")

STRONG_DEFAULT = "qwen3-coder:kai"
SMALL_DEFAULT = "qwen2.5:1.5b"

#: Task types that are analytical/deep by construction and must never route to
#: the small model, regardless of how short the raw query looks.
DEEP_TASK_TYPES = frozenset({
    "juris_deep",
    "juris_advocate", "juris_opponent", "juris_judge",
    "juris_advocate_fast", "juris_opponent_fast", "juris_judge_fast",
    "juris_case_analysis", "juris_argument_construction", "legal_argument",
})

#: Analytical intent markers — presence alone means "this is a hard ask".
COMPLEX_KEYWORDS = (
    "explain", "analyse", "analyze", "analysis", "compare", "comparison",
    "contrast", "draft", "discuss", "evaluate", "assess", "critically",
    "implications", "implication", "pros and cons",
    "advantages and disadvantages", "distinguish", "elaborate", "in detail",
    "step by step", "summarise", "summarize", "recommend", "advise",
    "legal opinion", "memorandum", "why", "how does", "construct an argument",
)

#: A query longer than either bound is treated as complex (safe default).
MAX_SIMPLE_WORDS = 14
MAX_SIMPLE_CHARS = 120

#: Falsy spellings for the boolean env switches.
_FALSEY = frozenset({"0", "off", "false", "no", "disabled"})

_RECENT: deque = deque(maxlen=50)
_VIABILITY_CACHE: dict = {}
_VIABILITY_TTL = 60.0


@dataclass(frozen=True)
class Route:
    """The routing decision for one generation call."""

    model: str
    complexity: str          # "simple" | "complex" | "forced"
    reason: str
    score: int
    small: bool
    task_type: str = ""

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "complexity": self.complexity,
            "reason": self.reason,
            "score": self.score,
            "small": self.small,
            "task_type": self.task_type,
        }


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def routing_mode() -> str:
    """One of ``on`` / ``off`` / ``auto`` (default ``auto``).

    ``auto`` is the shipped default because the small model is half-offloaded
    on the current P40 and loses to the resident 30B (see module docstring);
    ``on`` is available for hosts/operators that have verified the opposite.
    """
    raw = (os.environ.get("JURIS_KAI_ROUTING") or "auto").strip().lower()
    if raw in ("1", "on", "true", "yes", "enabled", "force"):
        return "on"
    if raw in ("0", "off", "false", "no", "disabled"):
        return "off"
    return "auto"


def routing_enabled() -> bool:
    return routing_mode() != "off"


def small_fallback_enabled() -> bool:
    return _env_flag("JURIS_KAI_SMALL_FALLBACK", True)


def strong_model() -> str:
    return (os.environ.get("JURIS_KAI_MODEL") or STRONG_DEFAULT).strip() \
        or STRONG_DEFAULT


def small_model() -> str:
    return (os.environ.get("JURIS_KAI_SMALL_MODEL") or SMALL_DEFAULT).strip() \
        or SMALL_DEFAULT


def is_small(model: str) -> bool:
    """Whether ``model`` is the configured small accelerator model."""
    if not model:
        return False
    return model == small_model() and model != strong_model()


def _probe_small_resident(model: str, now: float | None = None) -> bool:
    """True iff ``model`` is loaded and (almost) entirely in GPU VRAM.

    A model that is partially offloaded to CPU is *slower* than the resident
    30B, so it is not a speed win; ``auto`` must not use it. Result is cached
    briefly so routing stays a zero-latency decision.
    """
    now = time.time() if now is None else now
    cached = _VIABILITY_CACHE.get(model)
    if cached is not None and now - cached[0] < _VIABILITY_TTL:
        return cached[1]
    verdict = False
    try:
        base = (os.environ.get("JURIS_KAI_OLLAMA_URL")
                or "http://127.0.0.1:11434").rstrip("/")
        with urllib.request.urlopen(base + "/api/ps", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        family = model.split(":")[0]
        for entry in data.get("models", []):
            name = entry.get("name") or entry.get("model") or ""
            if name == model or name.split(":")[0] == family:
                size = float(entry.get("size") or 0)
                vram = float(entry.get("size_vram") or 0)
                verdict = bool(size and vram >= size * 0.95)
                break
    except Exception as exc:  # noqa: BLE001 - probe failure means "not viable"
        logger.debug("small-model residency probe failed: %s", exc)
        verdict = False
    _VIABILITY_CACHE[model] = (now, verdict)
    return verdict


def small_model_viable() -> bool:
    """Whether the small model is a safe, genuine speed win right now."""
    model = small_model()
    if not model or model == strong_model():
        return False
    override = os.environ.get("JURIS_KAI_SMALL_VIABLE")
    if override is not None:
        return override.strip().lower() not in _FALSEY
    return _probe_small_resident(model)


def _complex_reason(text: str, task_type: str, deep: bool) -> str:
    """Return a non-empty reason string when the query is too hard to downshift."""
    if deep or (task_type or "") in DEEP_TASK_TYPES:
        return "deep-or-analytical-task"
    text = (text or "").strip()
    if not text:
        return "no-query"
    low = text.lower()
    for keyword in COMPLEX_KEYWORDS:
        if keyword in low:
            return f"keyword:{keyword}"
    words = len(text.split())
    if words > MAX_SIMPLE_WORDS:
        return f"long-query:{words}words"
    if len(text) > MAX_SIMPLE_CHARS:
        return f"long-query:{len(text)}chars"
    clauses = (text.count(",") + text.count(";")
               + low.count(" and ") + low.count(" or "))
    if clauses >= 2:
        return "multi-clause"
    if text.count("?") > 1:
        return "multi-question"
    return ""


def classify(query: str | None = None, task_type: str = "",
             deep: bool = False) -> str:
    """Pure query-complexity label: ``"simple"`` or ``"complex"``.

    The deterministic, zero-network-call classifier the router is built on:
    a query is ``"simple"`` only when it is short, single-clause, has no
    analytical keyword, is not multi-part and is not Deep/deep-by-task-type.
    Everything ambiguous is ``"complex"`` (fail safe to the 30B). This is
    independent of the routing mode/residency gate — call it to *ask what the
    query is*, call :func:`route` to decide *which model answers it*.
    """
    text = (query or "").strip()
    return "complex" if _complex_reason(text, task_type, deep) else "simple"


def route(query: str | None = None, task_type: str = "",
          prompt: str = "", deep: bool = False) -> Route:
    """Decide which local model should answer. Pure; never touches the network."""
    task_type = task_type or ""
    forced = (os.environ.get("JURIS_KAI_FORCE_MODEL") or "").strip()
    if forced:
        return Route(forced, "forced", "force-model-env", 0,
                     is_small(forced), task_type)
    mode = routing_mode()
    if mode == "off":
        return Route(strong_model(), "complex", "routing-disabled", 0, False,
                     task_type)

    text = query if query is not None else prompt
    reason = _complex_reason(text or "", task_type, deep)
    if reason:
        return Route(strong_model(), "complex", reason, 1, False, task_type)
    if mode == "on" or small_model_viable():
        return Route(small_model(), "simple", "short-lookup", 0, True,
                     task_type)
    return Route(strong_model(), "complex", "small-not-viable", 0, False,
                 task_type)


def record(route_decision: Route, *, task_type: str = "",
           query: str | None = None, prompt: str = "") -> None:
    """Log one decision and append it to the observability ring."""
    text = query if query is not None else prompt
    task = task_type or route_decision.task_type
    entry = route_decision.as_dict()
    entry.update({"task_type": task, "chars": len(text or ""), "at": time.time()})
    _RECENT.append(entry)
    logger.info(
        "juris route model=%s complexity=%s reason=%s task=%s qchars=%d",
        route_decision.model, route_decision.complexity, route_decision.reason,
        task, len(text or ""))


def select_model(query: str | None = None, task_type: str = "",
                 prompt: str = "", deep: bool = False) -> str:
    """Route + record, returning just the model name (streaming convenience)."""
    decision = route(query, task_type=task_type, prompt=prompt, deep=deep)
    record(decision, task_type=task_type, query=query, prompt=prompt)
    return decision.model


def recent_routes(limit: int = 20) -> list[dict]:
    """Most recent routing decisions, oldest first (observability/tests)."""
    items = list(_RECENT)
    if limit is not None and limit >= 0:
        items = items[-limit:]
    return items
