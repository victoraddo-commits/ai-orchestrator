#!/usr/bin/env python3
"""Juris Kai cache warmer — top everyday-law questions.

Reuses the smoke-test pattern (tests/conftest.py, tests/juris_golden/runner.py):
drives the REAL bot entry point in-process with a fresh temp user per
question, the accounts DB in a temp dir, the disclaimer accepted up front,
and the fake-AI grader monkeypatch LEFT OFF so real grounded answers populate
the FAQ/generation caches:

  * juris_qa_log (durable FAQ layer, keyed faq_key(task_type, normalized
    query, "__generic__", source_key)) — survives restarts;
  * FAQ_CACHE / GENERATION_CACHE in-process TTL layers.

Every question is asked twice, each time from a FRESH user id:
  call 1 (cold)  -> real generation (~15 s), records the QA pair;
  call 2 (warm)  -> must hit the FAQ/generation cache (< 2 s) because
                    generic questions are replayable across accounts and
                    retrieval is deterministic (same source_key).
The completion report prints per-question cold/warm latencies and a final
summary line.

Set JURIS_KAI_DB_DIR before running to pin a shared DB dir (default: fresh
temp dir per run, like the smoke tests, so production memory is untouched).

Question phrasing is deliberately first-person-free: is_generic_question()
scopes shareable answers to "__generic__" only when no personal marker is
present.
"""
import os
import sys
import tempfile
import time
import uuid

sys.path.insert(0, "/opt/ai-orchestrator")
if not os.environ.get("JURIS_KAI_DB_DIR"):
    os.environ["JURIS_KAI_DB_DIR"] = tempfile.mkdtemp(prefix="juris_warm_")
os.environ.setdefault("JURIS_KAI_ADMIN_IDS", "999000001")

# 15 high-frequency everyday-law questions (Ghana).
QUESTIONS = [
    "How long can the police keep a suspect in detention without charge in Ghana?",
    "What are the duties of directors of a company in Ghana?",
    "What counts as unfair dismissal from employment in Ghana, and what remedies exist?",
    "What is the limitation period for suing to recover an unpaid debt in Ghana?",
    "Can a landlord evict a tenant without a court order in Ghana?",
    "Who inherits property when a person dies without a will in Ghana?",
    "What does the 1992 Constitution say about personal liberty?",
    "What is the process for filing a divorce in Ghana?",
    "What is the penalty for late filing of a company's annual returns in Ghana?",
    "Is consent required to process personal data under Ghana's Data Protection Act?",
    "Is hearsay evidence admissible in court in Ghana?",
    "What rights does a person arrested at a protest have in Ghana?",
    "How do you register a business in Ghana?",
    "What are the VAT obligations of a small business in Ghana?",
    "How do wills and probate work in Ghana?",
]

# Three questions reported explicitly in the final summary.
VERIFY_IDS = (0, 4, 8)

REFUSAL_MARKER = "so i won't guess"


def new_uid() -> str:
    return str(3_000_000_000 + (uuid.uuid4().int % 900_000_000))


def prepare(uid: str) -> None:
    """Create the account and accept the disclaimer before turn 1."""
    from core.juris_kai.accounts import get_account_manager
    import core.juris_kai.bot as bot

    try:
        get_account_manager().get_or_create(str(uid), f"Warm{uid[-3:]}")
    except Exception:
        pass
    bot.handle_callback({
        "id": "ack",
        "data": "disclaimer_accept",
        "from": {"id": int(uid)},
        "message": {"chat": {"id": int(uid)}},
    })


def ask(uid: str, text: str) -> dict:
    import core.juris_kai.bot as bot
    reply = bot.handle_message({
        "chat_id": uid, "text": text,
        "from_first_name": f"Warm{uid[-3:]}",
    })
    if reply and "acknowledge the disclaimer" in (reply.get("text") or ""):
        prepare(uid)
        reply = bot.handle_message({
            "chat_id": uid, "text": text,
            "from_first_name": f"Warm{uid[-3:]}",
        })
    return reply or {}


def main() -> int:
    import core.juris_kai.bot as bot

    # The warmer only needs handle_message's RETURN value. No-op the outbound
    # Telegram API so we never send to bogus temp chat ids (and never leak the
    # bot token into logs via failed-send tracebacks).
    bot.telegram_api = lambda method, data, timeout=35: {"ok": True}

    print("juris warm start: %d questions, db=%s, real AI (no fake grader)"
          % (len(QUESTIONS), os.environ["JURIS_KAI_DB_DIR"]), flush=True)
    rows = []
    for idx, question in enumerate(QUESTIONS):
        uid1 = new_uid()
        prepare(uid1)
        t0 = time.time()
        r1 = ask(uid1, question)
        cold = time.time() - t0
        text1 = r1.get("text") or ""

        uid2 = new_uid()
        prepare(uid2)
        t0 = time.time()
        r2 = ask(uid2, question)
        warm = time.time() - t0
        text2 = r2.get("text") or ""

        refused = REFUSAL_MARKER in text1.lower()
        # A fresh generation cannot finish in under 2 s on this stack, so
        # warm < 2 s proves the second call was served from the FAQ/generation
        # cache. Exact text equality is reported separately as a diagnostic
        # (answer assembly can vary slightly run to run).
        hit = (not refused) and warm < 2.0
        rows.append({"idx": idx, "cold": cold, "warm": warm, "hit": hit,
                     "len1": len(text1), "len2": len(text2),
                     "refused": refused})
        print("[%02d/15] cold=%.1fs warm=%.2fs %s len=%d replay_match=%s%s"
              % (idx, cold, warm, "HIT" if hit else "MISS", len(text1),
                 text2 == text1, " (refusal — not cacheable)" if refused else ""),
              flush=True)

    hits = sum(1 for r in rows if r["hit"])
    avg_cold = sum(r["cold"] for r in rows) / len(rows)
    avg_warm = sum(r["warm"] for r in rows) / len(rows)
    print("WARM-CACHE COMPLETE: %d/%d asked, %d/%d cache hits, "
          "avg cold=%.1fs avg warm=%.2fs, db=%s"
          % (len(rows), len(QUESTIONS), hits, len(QUESTIONS), avg_cold,
             avg_warm, os.environ["JURIS_KAI_DB_DIR"]), flush=True)
    for idx in VERIFY_IDS:
        r = rows[idx]
        print("VERIFY q%d: first-call %.1fs -> second-call %.2fs (%s, %dx faster)"
              % (idx, r["cold"], r["warm"], "cache hit" if r["hit"] else "miss",
                 max(1, round(r["cold"] / max(r["warm"], 0.01)))), flush=True)
    return 0 if all(r["hit"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
