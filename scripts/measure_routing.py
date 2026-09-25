#!/usr/bin/env python3
"""Measure small-model routing: simple vs hard latency (Phase E Task 2).

Local-only: retrieval from the legal-brain service, generation from the local
Ollama model(s). Never a cloud provider.

For each probe question this script:
  1. builds the *grounded* plan (the same strict gate the bot uses),
  2. prints the routing decision (model + complexity + reason),
  3. times the non-streamed answer with routing ON, then with routing forced to
     the 30B (``JURIS_KAI_ROUTING=off``) for the before/after,
  4. checks the answer is a real grounded answer (not the UNGROUNDED refusal),
     so routing is shown not to change grounding.

Run from the repo root with the venv::

    .venv/bin/python scripts/measure_routing.py
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("KAI_LEGAL_GAP_RECORD", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.juris_kai import grounding as g  # noqa: E402
from core.juris_kai import routing as r  # noqa: E402
from core.juris_kai import streaming as s  # noqa: E402

PROBES = [
    ("simple", "What is Article 19 of the 1992 Constitution?"),
    ("simple", "What is the penalty for stealing in Ghana?"),
    ("hard", "Explain the fundamental human rights provisions of the 1992 Constitution"),
    ("hard", "Compare the requirements for a valid contract and a valid will in Ghana"),
]


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def main() -> int:
    # Warm the small accelerator so cold model-load does not distort the first
    # simple-query number (ollama keeps it resident alongside the 30B).
    os.environ["JURIS_KAI_ROUTING"] = "on"
    try:
        s.generate("Warm up. Reply OK.", task_type="juris_flashcards",
                   query="warmup")
    except Exception as exc:  # noqa: BLE001 - best effort only
        print(json.dumps({"warmup_error": str(exc)}), file=sys.stderr)

    rows = []
    for label, query in PROBES:
        plan = g.build_grounded_plan(query, "juris_research")
        if plan["refusal"]:
            rows.append({"label": label, "query": query, "grounded": False,
                         "verdict": plan["verdict"], "note": "refused pre-model"})
            continue
        prompt = plan["prompt"]

        # Shipped-default verdict, for the report (default is now ``on``).
        default_decision = r.route(query, task_type="juris_research",
                                   prompt=prompt)

        # Force the downshift so the routed path is actually exercised.
        os.environ["JURIS_KAI_ROUTING"] = "on"
        decision = r.route(query, task_type="juris_research", prompt=prompt)
        t0 = time.perf_counter()
        text_on = s.generate(prompt, task_type="juris_research", query=query)
        ms_on = _ms(t0)

        os.environ["JURIS_KAI_ROUTING"] = "off"
        try:
            t0 = time.perf_counter()
            text_off = s.generate(prompt, task_type="juris_research", query=query)
            ms_off = _ms(t0)
        finally:
            os.environ.pop("JURIS_KAI_ROUTING", None)

        rows.append({
            "label": label,
            "query": query,
            "verdict": plan["verdict"],
            "grounded": bool(text_on) and text_on.strip() != g.UNGROUNDED_REPLY,
            "routed_model": decision.model,
            "complexity": decision.complexity,
            "reason": decision.reason,
            "default_model": default_decision.model,
            "default_reason": default_decision.reason,
            "latency_routed_ms": ms_on,
            "latency_30b_ms": ms_off,
            "speedup": round(ms_off / ms_on, 2) if ms_on else None,
            "words_routed": len(text_on.split()),
            "words_30b": len(text_off.split()),
        })
    print(json.dumps({"recent_routes": r.recent_routes(8), "rows": rows},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
