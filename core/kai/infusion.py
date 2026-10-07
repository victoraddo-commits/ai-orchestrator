"""Kai knowledge infusion (2026-10-07).

One brain, one body of knowledge: prefixes Kai's second-brain Knowledge Card
onto non-coding brain calls so the local model always carries its own house
knowledge (identity, architecture, legal corpus posture, recent decisions).
The card file lives at memory/kai_knowledge_card.md and is regenerated nightly
by tools/kai_distill_knowledge.py. Stable per-day prefix => ollama reuses the
cached prompt evaluation, amortizing the extra prefill to ~zero."""
import os, time, threading

CARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory", "kai_knowledge_card.md")

INFUSE_ROLES = {
    "text_task", "planning", "architecture", "review", "classification",
    "log_analysis", "documentation", "legal_rag", "law_research", "law_verify",
    "law_draft", "reasoning",
}

_card_cache = {"mtime": 0.0, "text": None}
_lock = threading.Lock()


def _read_card():
    with _lock:
        try:
            mt = os.stat(CARD_PATH).st_mtime
        except OSError:
            _card_cache.update(mtime=0.0, text=None)
            return None
        if mt != _card_cache["mtime"]:
            try:
                with open(CARD_PATH) as f:
                    txt = f.read().strip()
                _card_cache.update(mtime=mt, text=txt or None)
            except OSError:
                _card_cache.update(mtime=0.0, text=None)
        return _card_cache["text"]


_CARD_MARKER = "## Kai knowledge card"


def infuse(description: str, task_type=None):
    """Return (prompt, applied). Coding tasks are never infused (zero latency
    regression on the IDE-critical path); knowledge tasks get the card prefix
    unless it is already present (idempotent — no double infusion)."""
    task_type = (task_type or "").strip() or "text_task"
    if task_type not in INFUSE_ROLES and not task_type.startswith("law"):
        return description, False
    if _CARD_MARKER in (description or "")[:400]:
        return description, False
    card = _read_card()
    if not card:
        return description, False
    return f"{card}\n\n---\n\n{description}", True
