# HANDOFF — Juris Kai / legal search: fix A (keyword relevance)

**Date:** 2026-09-26
**State:** B done and pushed (`6bcf7af`). A diagnosed with proof, NOT yet implemented.
**Rule:** no guessing — phase 1 evidence below is authoritative.

## What is DONE (pushed `6bcf7af`, CT100 = LXC 100 `kai-legal-brain`)

- Default search mode is **hybrid** (was `or`); `mode=keyword` is a fast
  lexical path. Endpoint default fixed in `legal_brain_api.py`.
- **phrase** falls back phrase -> and -> or (never returns empty).
- `storage.search` now SELECTs `bm25()` and returns ordinal `bm25_rank`
  (was computed for ORDER BY but discarded).
- keyword results over-fetch (40) then rank via `ranking.rank_results`.
- every result carries `provenance` {document_id,title,citation,year,
  store_mode,match_strategy,matched_text}.
- citation lookup prefers content-rich copy over search_only stubs; adds
  `disambiguation` when a citation matches genuinely different instruments.
- tests: CT100 `845 passed`, incl. new `tests/test_search_modes.py`.

Repo mirrors: CT111 `/opt/ai-orchestrator/deploy/ct100/` (legal_brain_api.py,
core/legal/{storage,citations,embeddings}.py, tests/).

## What is BROKEN (fix A — keyword relevance)

Query "director duties" returns **Fisheries Act, 2002** first; the right answer
is the **Companies Act, 2019 (Act 992)**. "penalty for late filing" returns
Industrial Designs / Trade Marks.

## Root cause (PROVEN, do not re-derive)

The embedding model is FINE. Passage-level cosine:
    "director duties" vs a directors passage        = 0.747
    "director duties" vs a fisheries passage        = 0.500
Clear 0.25 separation.

But `hybrid_search` scores at DOCUMENT level:
    directior duties -> Fisheries Act  bm25_rank=12 dense=0.737 score=0.896
                        Companies Bill  bm25_rank=49 dense=0.730 score=0.612

Cause: `core/legal/hybrid.py::_safe_dense` takes the **max chunk similarity
per document**. A huge Act (Fisheries ~2000 chunks) has one incidental chunk
containing the query terms, and its max (~0.737) beats the relevant Act's max
(~0.730). Document-level max destroys the passage-level signal.

Evidence commands (run ON CT100 `/opt/kai-legal-brain`):
    python3 /tmp/dh.py     # hybrid per-result bm25_rank/dense/score
    python3 /tmp/de.py     # passage-level cosines (0.747 vs 0.500)
(task-local scripts; recreate if gone — see "Reproduce" below)

## Fix A (proposed, NOT implemented)

1. Rank on **passage-level** similarity: keep the best-matching chunk id +
   score per document, and sort by that, not by a document average/max that
   lets one incidental chunk dominate a large Act.
2. Return **the best passage** as both `snippet` and `provenance.matched_text`
   (already wired — provenance picks the sentence with most query terms).
3. Consider a **length-normalised** dense score so a 2000-chunk Act is not
   penalised/advantaged purely by size.
4. Re-measure: "director duties" must put Companies Act 992 in the top 3;
   "penalty for late filing" must reach the relevant tax/companies provisions.
5. Add a regression test asserting those two queries' top-3.

Files to change: `core/legal/hybrid.py` (`_safe_dense`, fusion),
maybe `core/legal/embeddings.py` (passage payload). Mirror into
CT111 `deploy/ct100/`.

## Reproduce the evidence

```bash
export PVEB='ssh -i /root/.ssh/pve2_deploy -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.110'
# run a script on CT100
$PVEB "pct exec 100 -- sh -c 'cd /opt/kai-legal-brain && python3 /tmp/<script>.py'"
```
`/tmp/opencode/dbg_hybrid.py` and `/tmp/opencode/dbg_emb.py` on LXC113 are the
source of the two scripts above.

## Access / guardrails

- CT100 has NO git; changes are mirrored into CT111 `deploy/ct100/` and
  committed there (branch `runner-kai-2.0-20260918`).
- Back up before editing: `cp <f> <f>.bak-<ts>` (pattern already used).
- GhaLII is BLOCKED (paywalled) — `BLOCKED_DOMAINS` in
  `core/klaus/source_registry.py`. Do not re-add.
- Deploy pattern that works: write b64 on PVE-B, `pct push 100`, decode in CT100,
  restart `kai-legal-brain`, verify `/health`.
