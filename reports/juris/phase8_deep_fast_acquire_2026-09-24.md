# Phase 8 — Deep Fast mode + validated Acquire path (2026-09-24)

Runner: LXC 111 `/opt/ai-orchestrator`, branch `runner-kai-2.0-20260918`.
Model: VM104 P40 `qwen3-coder:kai` (resident; no VM104 config changes).

## Gap 1 — Deep latency

Thorough Deep ran advocate ∥ opponent (two concurrent 500-token passes on one
GPU → contention) then a 500/800-token judge: ~33–36 s.

**Fast mode** (`reasoning.run_deep(..., fast=True)`):
- advocate on a 300-token budget,
- opponent is **retrieval-only** (0 GPU tokens): contrary authority + contrary
  spans come from `_retrieve_contrary` / `_find_contrary_spans`, so the single
  GPU serves only the advocate and the judge — the root cause of contention,
- judge on a 500-token budget.
Budgets are configurable via `prompt.set_budget()` and the
`JURIS_KAI_TOKEN_BUDGETS` env JSON; fast task types are
`juris_{advocate,opponent,judge}_fast`.

The judge pass can stream (`stream_judge=True`) for low time-to-first-token; the
collected text is still citation-firewalled, so streaming never weakens the
no-ungrounded guarantee.

Measured live (same model, same retrieval; `scripts/measure_deep_fast.py`):

| Query | Mode | retrieve | adv ∥ opp | judge | total | judge TTFT | user TTFT |
|---|---|---|---|---|---|---|---|
| rape | thorough | 2.63 s | 18.76 s | 12.05 s | **33.44 s** | — | — |
| rape | **fast (streamed)** | 0.48 s | 6.80 s | 7.96 s | **15.24 s** | 0.72 s | 8.00 s |
| human rights | thorough | 0.96 s | 23.19 s | 11.44 s | **35.59 s** | — | — |
| human rights | **fast (streamed)** | 0.74 s | 6.47 s | 6.82 s | **14.03 s** | 0.81 s | 8.02 s |

Fast meets the ~18–22 s target with margin (~14–15 s). "User TTFT" is the
time-to-first-visible-judge-text = retrieve + advocate∥opponent + judge TTFT;
the judge itself starts emitting in ~0.7–0.8 s, but the pipeline cannot emit
final-answer text before the advocate pass finishes, so a true sub-second
end-to-end TTFT would require streaming a preliminary pass (deliberately not
done — it would show a partial answer that is then rewritten).

Surfaces: `POST /api/legal/ask` accepts `deep=true&fast=true` and
`stream=true` (SSE: `status` → `delta` → `final`); the Command Center Ask tab
has a "Deep Fast (streamed)" option; the Telegram bot has a ⚡ Deep Fast menu
button and `/deepfast`.

## Gap 2 — Acquire validation

Real, bounded, polite acquire on the live pending gap (WAL-safe backup on the
brain), via `scripts/legal_gap_acquire.py --limit 3 --per-source 3 --delay 0.5`:

- gap #3 "Traffic & vehicle tint" → **needs_review**
- sources_tried: `parliament-dspace, dvla.gov.gh, nrsa.gov.gh`
- brain evidence: `{"reason": "only pre-existing documents matched; the missing
  instrument was not found"}`
- notifications fired (gaps #2, #3 needs_review; askers non-numeric).

Nothing on the live parliament-dspace lane was genuinely fillable at validation
time (the corpus already holds every enactment the lane returned), so the
`filled` path is proven with a controlled fixture integration test
(`tests/test_juris_gap_acquire.py::FilledBrain`): a fake brain *ingests* a
lawfully-found enactment, marks the gap `filled`, and the pass notifies the
asker + operator naming the instrument — with the one-shot
`mark_gap_notified` guard asserted. `test_acquire_surfaces_filled_result`
proves the CC acquire route relays `status`/`doc_ids`/`sources_tried`/`evidence`.
The CC Gaps tab renders those fields (status badge, sources_tried, doc_ids,
reason/evidence, raw JSON).

## Verification

- `pytest -k "juris or legal or cc_legal or acquire or gap or reasoning or ..."`:
  **814 passed, 1 skipped**, 14 subtests passed. No regressions.
- New: `tests/test_juris_deep_fast.py` (16), plus added cases in
  `test_cc_legal_routes.py`, `test_juris_deep_bot.py`,
  `test_juris_gap_acquire.py`.

## Honest gaps

- Fast end-to-end user TTFT is ~8 s, not sub-second; the judge token stream
  itself is ~0.7 s. A 2-pass design was chosen over "advocate ∥ compact
  opponent" because it removes GPU contention and measured faster.
- The live `filled` path could not be exercised end-to-end (no genuinely absent
  enactment on the lane); it is proven by mocked-source integration tests.
- `scripts/measure_deep_fast.py` is a read-only validation harness.
