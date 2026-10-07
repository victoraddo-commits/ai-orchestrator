#!/usr/bin/env python3
"""v3: real decision/lesson fields (problem/title/recommendation/subject)."""
import json, os, time, urllib.request

BASE = "/project/ai-orchestrator"
CARD = f"{BASE}/memory/kai_knowledge_card.md"
MAX_CHARS = 1600

def _clean(v, n=140):
    s = str(v).strip().replace("\n", " ")
    if s.startswith(("{", "[", "b'")):
        return None
    return s[:n] if len(s) >= 8 else None

def legal_stats():
    try:
        d = json.loads(urllib.request.urlopen("http://192.168.1.100:8100/health", timeout=5).read())
        return d.get("documents", d.get("doc_count", "?"))
    except Exception:
        return "?"

def distill():
    parts = []

    # decisions: newest non-trivial, by title/problem
    try:
        d = json.load(open(f"{BASE}/memory/decisions.json"))
        picks = []
        for r in reversed(d.get("records", [])):
            for k in ("title", "problem", "decision", "summary", "subject"):
                v = _clean(r.get(k))
                if v:
                    picks.append(f"decision[{r.get('status','?')}]: {v}")
                    break
            if len(picks) >= 3: break
        if picks:
            parts.append("### Recent decisions\n" + "\n".join(f"- {p}" for p in picks))
    except Exception:
        pass

    # lessons: newest 3 by subject/recommendation
    try:
        d = json.load(open(f"{BASE}/memory/learning_lessons.json"))
        picks = []
        for r in reversed(d.get("records", [])):
            for k in ("subject", "lesson", "recommendation"):
                v = _clean(r.get(k))
                if v:
                    picks.append(f"{r.get('category','lesson')}: {v}")
                    break
            if len(picks) >= 3: break
        if picks:
            parts.append("### Lessons learned\n" + "\n".join(f"- {p}" for p in picks))
    except Exception:
        pass

    card_body = ("\n\n".join(parts)) if parts else "(no recent decision/lesson records)"

    card = f"""## Kai knowledge card (v{time.strftime('%Y%m%d')}, distilled {time.strftime('%Y-%m-%d %H:%M')})
You are Kai, one unified local intelligence. Your self-facts:
- Brain model: qwen3-coder:kai (Qwen3-MoE 30B-A3B) on VM104 Tesla P40 (ollama :11434 via tunnels); raw llama.cpp 68.8 tok/s; all Kai roles (kai_brain/kai_coder/local) route to this one model — local-only, no third-party providers.
- Second brain: core/second_brain stores (project 780+ records, operational 172, cognitive) + ~96 memory json files (decisions, lessons).
- Legal brain: Ghana corpus, CT100 :8100 — {legal_stats()} documents; search via GET /search?q=&limit= when precision matters. Answers advisory, never official legal advice.
- Watchdog: endpoint-dead -> tunnel restart -> ollama restart; cold brain -> preload, NEVER restart mid-load.
- Network: SITE-A = Proxmox A (192.168.99.2: OPNsense, CT100 claude-code, CT105 ohio); SITE-B = Proxmox B (192.168.1.110: VM104/VM112, CT100-116). Tailscale mesh connects them.

{card_body}"""
    if len(card) > MAX_CHARS:
        card = card[:MAX_CHARS].rsplit("\n", 1)[0] + "\n(truncated to fit)"
    tmp = CARD + ".tmp"
    with open(tmp, "w") as f: f.write(card)
    os.replace(tmp, CARD)
    print(f"CARD v3 WRITTEN: {len(card)} chars")

if __name__ == "__main__":
    distill()
