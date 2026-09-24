#!/usr/bin/env python3
"""Measure Deep thorough vs fast latency for Phase 8 validation (read-only)."""
import json
import sys
import time

sys.path.insert(0, "/opt/ai-orchestrator")

from core.juris_kai import reasoning, streaming  # noqa: E402

QUERIES = [
    ("rape", "What is the offence of rape under Ghanaian criminal law?"),
    ("human-rights",
     "What fundamental human rights are guaranteed by the 1992 Constitution of Ghana?"),
]


def run_one(query, fast, stream_judge):
    chunks = []
    ttfts = []
    t0 = time.perf_counter()
    if stream_judge:
        res = reasoning.run_deep(
            query, fast=fast, stream_judge=True,
            on_judge_chunk=chunks.append,
            on_judge_ttft=ttfts.append)
    else:
        res = reasoning.run_deep(query, fast=fast)
    wall = round(time.perf_counter() - t0, 3)
    lat = res.get("latency") or {}
    judge_ttft = lat.get("judge_ttft")
    pre = lat.get("retrieve", 0.0) + lat.get(
        "parallel", lat.get("advocate", 0.0))
    user_ttft = round((pre + judge_ttft) * 1000, 1) if judge_ttft else None
    return {
        "fast": fast,
        "verdict": res.get("verdict"),
        "docs": len(res.get("docs") or []),
        "degraded": res.get("degraded"),
        "latency": lat,
        "wall": wall,
        "chunks": len(chunks),
        "judge_ttft": judge_ttft,
        "user_ttft_ms": user_ttft,
    }


def main():
    print("model_available:", streaming.available(), file=sys.stderr)
    out = {"queries": {}}
    for label, q in QUERIES:
        row = {"query": q, "runs": {}}
        row["runs"]["thorough"] = run_one(q, fast=False, stream_judge=False)
        row["runs"]["fast_streamed"] = run_one(q, fast=True, stream_judge=True)
        out["queries"][label] = row
        print(json.dumps({label: row}, indent=1), flush=True)
    print("RESULT_JSON:" + json.dumps(out))


if __name__ == "__main__":
    main()
