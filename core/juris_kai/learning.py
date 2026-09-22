"""Weak-area mining for the Juris Kai learning loop.

Read-only analysis over the local ``juris_qa_log`` table (CT111 only). It
surfaces what users actually ask, which answers were weak/blocked, and which
questions repeat — i.e. candidates for better canned answers or retrieval.

Nothing here calls a provider or an external service; it is pure SQLite +
in-process heuristics. Exposed read-only via
``GET /api/juris-kai/cc/learning`` and surfaced in the Command Center.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger("juris_kai.learning")

WEAK_CONFIDENCE = 0.5
WEAK_ANSWER_CHARS = 120
DEFAULT_LIMIT = 1000


def _rows(limit: int) -> List[Dict[str, Any]]:
    from core.juris_kai.accounts import get_account_manager
    mgr = get_account_manager()
    cur = mgr.db.execute(
        "SELECT account_id, task_type, question, answer, confidence, "
        "question_hash, cache_eligible, cache_hit, latency_ms, created_at "
        "FROM juris_qa_log ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in cur.fetchall()]


def analyze(limit: int = DEFAULT_LIMIT, top: int = 10,
            min_count: int = 2) -> Dict[str, Any]:
    """Mine the Q&A log into an actionable learning report."""
    from core.juris_kai import cache as _cache

    rows = _rows(limit)
    total = len(rows)

    by_task: Dict[str, int] = {}
    question_counts: Dict[str, int] = {}
    question_sample: Dict[str, str] = {}
    question_task: Dict[str, str] = {}
    weak: List[Dict[str, Any]] = []
    blocked = 0
    cache_hits = 0
    eligible = 0

    for r in rows:
        task = r.get("task_type") or "unknown"
        by_task[task] = by_task.get(task, 0) + 1
        if r.get("cache_hit"):
            cache_hits += 1
        if r.get("cache_eligible"):
            eligible += 1

        norm = _cache.normalize_query(r.get("question") or "")
        if norm:
            question_counts[norm] = question_counts.get(norm, 0) + 1
            question_sample.setdefault(norm, (r.get("question") or "")[:200])
            question_task.setdefault(norm, task)

        answer = (r.get("answer") or "")
        conf = r.get("confidence")
        conf = float(conf) if conf is not None else _cache.answer_confidence(answer)
        is_blocked = _cache.answer_is_blocked(answer)
        if is_blocked:
            blocked += 1
        if conf < WEAK_CONFIDENCE or len(answer.strip()) < WEAK_ANSWER_CHARS:
            weak.append({
                "question": (r.get("question") or "")[:200],
                "task_type": task,
                "confidence": round(conf, 3),
                "answer_chars": len(answer.strip()),
                "blocked": is_blocked,
                "created_at": r.get("created_at"),
            })

    most_asked = sorted(
        ({"question": q, "count": c, "sample": question_sample.get(q, ""),
          "task_type": question_task.get(q, "")}
         for q, c in question_counts.items()),
        key=lambda x: x["count"], reverse=True,
    )

    repeat_questions = [m for m in most_asked if m["count"] >= int(min_count)][:top]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_records": total,
        "by_task_type": dict(sorted(by_task.items(),
                                    key=lambda kv: kv[1], reverse=True)),
        "top_topics": [{"topic": t, "count": c}
                       for t, c in sorted(by_task.items(),
                                          key=lambda kv: kv[1], reverse=True)][:top],
        "most_asked": most_asked[:top],
        "repeat_questions": repeat_questions,
        "weak_answers": weak[:top],
        "weak_count": len(weak),
        "blocked_answers": blocked,
        "cache_hits": cache_hits,
        "cache_eligible": eligible,
        "cache_hit_rate": round(cache_hits / total, 4) if total else 0.0,
    }


def report() -> Dict[str, Any]:
    """Public alias used by the read-only CC route."""
    try:
        return analyze()
    except Exception as exc:  # noqa: BLE001 - admin surface must not 500
        logger.error("juris learning report failed: %s", exc)
        return {"error": str(exc), "total_records": 0,
                "repeat_questions": [], "weak_answers": [],
                "most_asked": [], "by_task_type": {}}
