"""Kai knowledge infusion v2 (2026-10-07).

One brain, one body of knowledge:
1. Static: prefixes the second-brain Knowledge Card onto non-coding brain calls.
2. Topic-aware: when a task touches Ghanaian law, pulls matching corpus snippets
   from the legal brain (:8100) into the same context at ask time.
Stable per-day prefix => ollama reuses the cached prompt evaluation; the legal
block is cached per-topic (TTL) so repeated topics cost nothing extra."""
import os, time, threading, urllib.parse

CARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory", "kai_knowledge_card.md")

INFUSE_ROLES = {
    "text_task", "planning", "architecture", "review", "classification",
    "log_analysis", "documentation", "legal_rag", "law_research", "law_verify",
    "law_draft", "reasoning",
}

LEGAL_URL = os.environ.get("KAI_LEGAL_BRAIN_URL", "http://192.168.1.100:8100")
LEGAL_TTL = 86400.0
LEGAL_SNIPPETS = 2
LEGAL_BLOCK_CHARS = 700

_card_cache = {"mtime": 0.0, "text": None}
_lock = threading.Lock()
_LEGAL_CACHE = {}

def _http_get(url, timeout=3.0):
    import urllib.request
    return urllib.request.urlopen(url, timeout=timeout).read().decode()

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

def _is_legal_topic(text: str) -> bool:
    t = (text or "").lower()
    if "ghana" in t and any(w in t for w in (
            "law", "legal", "court", "act", "constitution", "statute",
            "charge", " detain", "arrest", "fine", "court rule", "complain",
            "sentence", "judgment", "judge", "bail", "extradition", "labour",
            "contravention", "offence")):
        return True
    return any(w in t for w in (
        "constitution", "which ghana act", "ghana act", "legally", "lawsuit",
        "statute", "case law", "judgment", "offence"))


def _retrieve_legal(text: str):
    t = _is_legal_topic(text)
    if not t:
        return None
    q = " ".join((text or "").split())[:120]
    now = time.time()
    hit = _LEGAL_CACHE.get(q)
    if hit and (now - hit[0]) < LEGAL_TTL:
        return hit[1]
    try:
        raw = _http_get(f"{LEGAL_URL}/search?q={urllib.parse.quote(q)}&limit={LEGAL_SNIPPETS}",
                        timeout=3.0)
        import json
        data = json.loads(raw)
        lines = []
        for r in (data.get("results") or [])[:LEGAL_SNIPPETS]:
            title = (r.get("title") or "").strip()
            snip = (r.get("snippet") or "").strip().replace("\n", " ")[:160]
            if title:
                lines.append(f"- {title}: {snip}")
        block = None
        if lines:
            block = "### Relevant Ghana laws (corpus, via legal brain; verify citation)\n" + "\n".join(lines)
            block = block[:LEGAL_BLOCK_CHARS]
        _LEGAL_CACHE[q] = (now, block)
        return block
    except Exception:
        _LEGAL_CACHE[q] = (now, None)
        return None


def infuse(description: str, task_type=None):
    """Return (prompt, applied). Coding tasks are never infused (zero latency
    regression); knowledge tasks get the stable card prefix (idempotent) and,
    when the topic is legal, matching corpus snippets at ask time."""
    task_type = (task_type or "").strip() or "text_task"
    if task_type not in INFUSE_ROLES and not task_type.startswith("law"):
        return description, False
    if _CARD_MARKER in (description or "")[:800]:
        return description, False
    card = _read_card()
    if not card:
        return description, False
    legal_block = _retrieve_legal(description)
    prefix = card
    if legal_block:
        prefix = f"{card}\n\n{legal_block}"
    return f"{prefix}\n\n---\n\n{description}", True
