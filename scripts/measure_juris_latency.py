#!/usr/bin/env python3
"""Measure Juris Kai latency: retrieval, non-streamed, streamed, cache hit.

Local-only — this script talks to the legal-brain service and the local Ollama
model, never a cloud provider. Run from the orchestrator root with the venv:

    .venv/bin/python scripts/measure_juris_latency.py ["fixed legal query"]

Prints a JSON report with:
  * retrieval_ms           — legal-brain /search
  * nonstreamed_miss_ms    — full local answer, no streaming, cache cold
  * streamed_ttft_ms       — time to first streamed token
  * streamed_total_ms      — full streamed answer
  * cache_hit_ms           — same query served from the generation TTL cache
  * words / tokens_estimate
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_QUERY = "Explain the fundamental human rights provisions of the 1992 Ghana Constitution"


def _ms(start):
    return round((time.time() - start) * 1000, 1)


def main():
    query = " ".join(sys.argv[1:]) or DEFAULT_QUERY

    from core import legal_brain_client as lb
    from core.juris_kai import streaming as jstream
    from core.juris_kai import cache as jcache
    from core.juris_kai.prompt import build_prompt, budget_for

    report = {"query": query, "ollama_url": jstream.OLLAMA_URL,
              "model": jstream.DEFAULT_MODEL}

    # 1. Retrieval
    t0 = time.time()
    try:
        hits = lb.search(query, limit=3)
        report["retrieval_ms"] = _ms(t0)
        report["retrieval_hits"] = len(hits)
    except Exception as exc:
        report["retrieval_ms"] = None
        report["retrieval_error"] = str(exc)

    prompt = build_prompt("legal_research", query)
    report["prompt_chars"] = len(prompt)
    report["budget_tokens"] = budget_for("juris_research")

    # 2. Non-streamed cold generation
    t0 = time.time()
    try:
        text = jstream.generate(prompt, task_type="juris_research")
        report["nonstreamed_miss_ms"] = _ms(t0)
        report["words"] = len(text.split())
    except Exception as exc:
        report["nonstreamed_miss_ms"] = None
        report["nonstreamed_error"] = str(exc)
        text = ""

    # 3. Streamed cold generation (fresh cache key is irrelevant here; we
    #    bypass the cache and hit the model directly)
    ttft = None
    t0 = time.time()
    acc = ""
    try:
        for piece in jstream.stream_chat(prompt, task_type="juris_research"):
            if ttft is None:
                ttft = _ms(t0)
            acc += piece
        report["streamed_ttft_ms"] = ttft
        report["streamed_total_ms"] = _ms(t0)
        report["streamed_words"] = len(acc.split())
    except Exception as exc:
        report["streamed_ttft_ms"] = ttft
        report["streamed_total_ms"] = None
        report["streamed_error"] = str(exc)

    # 4. Cache hit (store what the streamed path produced, then read it)
    jcache.GENERATION_CACHE.clear()
    corpus = jcache.corpus_version()
    key = jcache.generation_key("juris_research", query, corpus)
    jcache.GENERATION_CACHE.set(key, {"text": acc or text, "model": jstream.DEFAULT_MODEL,
                                      "corpus_version": corpus})
    t0 = time.time()
    hit = jcache.GENERATION_CACHE.get(key)
    report["cache_hit_ms"] = _ms(t0)
    report["cache_hit"] = bool(hit and hit.get("text"))
    report["corpus_version"] = corpus

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
