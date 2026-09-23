# Juris Kai — Grounding-First Design

**Date:** 2026-09-23
**Status:** Draft for owner review
**Author:** Kai (OpenCode session)
**Scope:** Make Juris Kai answers trustworthy, source-backed, and verifiable; make the corpus correct; then improve formatting/UX.

---

## 1. Problem statement

Juris Kai (Telegram legal assistant) currently answers Ghana-law questions with **confident but ungrounded** prose. Investigation of the live production path produced:

- **Fabricated citations.** `"Contracts Act 1960 (Act 29/60)"` (real: Act 25); invented case citations `R v. H. G. M. B. v. The DPP [1980] 2 GLR 254`, `Ghana Electricity Company Ltd v. Kwame Agyeman [1987] 2 GLR 456`; wrong constitutional articles (Art 87/10 instead of Art 19).
- **Broken retrieval.** CT100 `storage.py::search` passes the raw query to SQLite FTS5 `MATCH`, so any `,` `(` `)` `?` raises `fts5: syntax error` → HTTP 500 → fallback DB path that does not exist on CT111 → **0 sources**. Long natural-language queries hit implicit-AND → 0 sources. Even on success, `snippet(fts_documents, 2, …)` reads the **`court`** column (content is index 0), so the "source text" is literally `"""Parliament"""`.
- **No deterministic citations.** No "Sources" section is ever rendered; citations are model-invented.
- **No grounding signal.** No banner when nothing was retrieved; no per-answer disclaimer.
- **Corpus contamination.** 586/845 FTS rows have empty content; 349 docs claim non-search_only store_mode with empty content (98 claim `full`); 458 have `citation == title`; 322 have `court='Parliament'` on non-acts; 21 press releases misclassified as law; 3 test docs; only **135** documents are usable law.

**Owner directive:** answers must never state things that cannot be backed by a source; sources must be quoted so answers are verifiable; the corpus must be correct.

---

## 2. Goals / non-goals

### Goals
1. **No ungrounded legal answers.** If no authoritative source backs the substance, the bot does not give legal substance.
2. **Verifiable answers.** Every answer carries deterministic, retrieval-derived citations **and verbatim quoted source text**.
3. **Correct corpus.** Junk/quarantined, metadata fixed, store_mode truthful, and a validation gate so contamination cannot recur.
4. **Good formatting.** Structured, Markdown/HTML-safe, with disclaimer and source footer.
5. **Serve users better.** Learn from unanswered questions to drive corpus priorities.

### Non-goals
- No third-party/cloud providers (local-only remains absolute).
- No paid legal databases.
- Not building a lawyer; the bot remains an advisory research/tutor tool.
- No corpus ingestion of user uploads into the authoritative corpus (existing zero-trust split stays).

---

## 3. Architecture

```
user query
  │
  ├─▶ Query normalization        strip FTS5 metachars, tokenize, build safe MATCH
  │
  ├─▶ Progressive retrieval      phrase → AND → OR(ranked) → title/citation fallback
  │      (CT100 /search; local fallback must be real or fail loudly)
  │
  ├─▶ Grounding verdict          GROUNDED | PARTIAL | UNGROUNDED  (+ per-source quality)
  │
  ├─▶ Prompt build               ONLY retrieved sources, quoted verbatim, tier instruction
  │
  ├─▶ Local model generate       qwen3-coder:kai (streaming)
  │
  ├─▶ Citation guard             validate every citation vs retrieved sources + registry
  │
  ├─▶ Verifiable footer          deterministic Sources + verbatim quotes (from retrieval)
  │
  └─▶ Render                     HTML-safe template + tier banner + disclaimer
```

**Key principle:** the *grounding verdict* and the *Sources footer* come from **retrieval**, never from the model. The model may only cite what retrieval supplied.

---

## 4. Component design

### 4.1 Phase 0 — Corpus integrity (prerequisite)

**Why first:** grounding is impossible while 69% of the corpus is empty and 21% of metadata is garbage. This phase makes the rest meaningful.

**4.1.1 Corpus audit tool** (`core/legal/corpus_audit.py` on CT100 + repo copy)
- Classifies every document: `usable` / `empty` / `mislabeled` / `junk` / `metadata_bad`.
- Emits a report (JSON + human) with counts and per-doc reasons.

**4.1.2 Remediation** (`scripts/remediate_corpus.py`, idempotent, dry-run by default)
- **Quarantine** (not delete): test docs (`Ok`, `Test v The State`, `Draft, no source`), press releases/non-law scrapes, HTML-entity titles. Move to a `quarantine` table/flag with reason; never silently delete.
- **Fix store_mode truth:** empty content can never be `full`/`reference`. Downgrade empty → `search_only` (or `quarantined`).
- **Fix metadata:** `year=0`/null → derive from title/citation or mark unknown; `citation == title` → derive from title or mark `unverified`.
- **Deduplicate** by normalized title + citation.
- Produce a before/after report; require owner sign-off before destructive changes.

**4.1.3 Ingestion validation gate** (`core/legal/validation.py`, enforced on every ingest path)
- Reject/quarantine: empty or <N-char content, non-legal content (heuristic), missing title, malformed citation.
- Enforce `store_mode` consistency with content length and rights basis.
- Log every rejection with reason to `rights_audit`/`ingest_audit`.

**4.1.4 Source hygiene**
- Audit `sources.json`; fix the Parliament OAI source that emits press releases (filter by type/collection), or disable if it cannot be filtered.

**Acceptance:** usable-law count materially increases; zero empty-content docs claiming `full`; test docs and press releases quarantined; validation gate blocks a synthetic bad doc.

---

### 4.2 Phase 1 — Retrieval & grounding enforcement

**4.2.1 FTS fix (CT100 `core/legal/storage.py`)**
- **Escape/quote** the query for FTS5 (build a safe MATCH expression from tokens; never pass raw user text).
- **Fix snippet column** to `content` (index 0), and **strip `<mark>` tags**.
- Return `store_mode`, `rights_basis`, and the real content excerpt in `/search` results.

**4.2.2 Progressive retrieval** (`core/juris_kai/legal_context.py`)
- Stage 1: quoted phrase (if multi-word).
- Stage 2: AND of significant tokens.
- Stage 3: OR of significant tokens, ranked.
- Stage 4: title/citation keyword fallback.
- Stop at first stage yielding ≥1 quality hit; record which stage and the top score.

**4.2.3 Grounding verdict**
- `GROUNDED`: ≥1 source with real content and a relevant score.
- `PARTIAL`: sources exist but coverage/quality below threshold.
- `UNGROUNDED`: no usable source.

**4.2.4 Strict no-ungrounded policy (owner directive)**
- **UNGROUNDED ⇒ the bot gives no legal substance.** It responds with a clear, honest message: no authoritative source found in the database; offers to rephrase, lists nearby/available topics, or states the topic is not yet covered. It must **not** answer the legal question from model memory.
- **PARTIAL ⇒** answer only what the sources support; explicitly mark anything not source-backed and forbid specific citations for it.
- No fabricated citations in any tier.

**Acceptance:** punctuation queries no longer 500; a `bail`/`Act 29` query returns real content; an ungrounded question returns the honest no-source response with zero legal claims; a grounded question returns only source-backed claims.

---

### 4.3 Phase 2 — Verifiable answers (quotes + citation guard)

**4.3.1 Verbatim source quoting**
- The prompt instructs the model to support each substantive point with a **short verbatim quote** from the provided source text.
- The **Sources footer is built deterministically** from retrieval: title, citation, year, court, `store_mode`, and a short quoted excerpt — independent of the model.

**4.3.2 Citation guard** (`core/juris_kai/citation_guard.py`)
- Extract citations from the answer: `Act \d+`, `PNDCL \d+`, `[YYYY] GLR`, `Article \d+`, `s.\d+`, case-name patterns.
- Classify each: **verified** (matches a retrieved source or the constitution registry) / **unverified**.
- Unverified → strip, or mark `[unverified — not found in database]`. Never leave an invented authority unmarked.

**4.3.3 Answer template**
- `Short answer → Authority (with quotes) → Application → Caveat`.
- Enforced by prompt + light structural post-check (not brittle parsing).

**Acceptance:** every grounded answer shows a Sources footer with real quotes; a synthetic answer containing a fake citation has it stripped/flagged; template renders on the main flows.

---

### 4.4 Phase 3 — Formatting & UX

- **Render in HTML `parse_mode`** with `html.escape` (robust vs legacy Markdown breaking on `_`/`*`/`[`); keep a plain-text fallback.
- **Tier banner** at top when PARTIAL/UNGROUNDED.
- **One-line disclaimer** appended to every answer (not just onboarding).
- **UTF-16-aware splitting** with continuation markers; keyboard preserved on the final chunk.
- **Surface cache hits** subtly ("from memory") and never share a personalized answer across accounts.
- Fix error strings that leak the user's full question.

**Acceptance:** citations with `_`/`*` render correctly; long answers split cleanly with a continuation marker; disclaimer present; no full-question leakage in errors.

---

### 4.5 Phase 4 — Serve users better

- Mine `juris_qa_log` for **UNGROUNDED questions** → ranked "corpus gaps" report (admin/CC).
- Feed that list into harvest priorities so the most-asked unanswered topics get ingested first.
- Optionally show users "this topic isn't covered yet" so expectations are honest.

**Acceptance:** a report lists top unanswered questions; harvest queue reflects them.

---

## 5. Data / interfaces

- **CT100 `/search`** response gains: `store_mode`, `rights_basis`, `content_excerpt` (clean), `match_stage`, `score`.
- **CT111 `legal_context.py`** gains: `retrieve(query) -> {docs, verdict, stage}`, `build_sources_footer(docs)`, `grounding_verdict(docs)`.
- **New modules:** `core/legal/corpus_audit.py`, `core/legal/validation.py`, `core/juris_kai/citation_guard.py`, `core/juris_kai/render.py`.
- **Prompt** carries explicit tier + "quote your sources" + "no citation outside provided sources".
- Guard module (`core/legal/injection.py`) stays in sync across CT100/CT111/repo (existing `scripts/sync_injection_guard.sh`).

---

## 6. Error handling

- CT100 unreachable → **fail loudly**, do not silently fall back to a non-existent local DB (the current bug). Return an honest "knowledge base unavailable" response, never an ungrounded answer.
- FTS error → normalized/escaped query retry; if still failing, log and treat as UNGROUNDED (no legal substance).
- Model timeout/empty → existing honest error strings (de-leaked).
- Citation guard failure → fail closed (strip unverifiable citations).

---

## 7. Testing strategy (TDD)

- **CT100:** FTS escaping unit tests (punctuation, quotes, operators); snippet column/content tests; store_mode consistency.
- **Corpus:** audit classification fixtures; remediation dry-run assertions; validation-gate rejection tests; no-empty-full invariant.
- **CT111:** progressive-retrieval tests per stage; grounding-verdict tests; **no-ungrounded-substance** test (asserts zero legal claims when no sources); citation-guard tests (fake citation stripped/flagged); footer determinism; render/HTML-escaping tests; UTF-16 splitting.
- **Regression:** full-suite gate (`scripts/test_regression_gate.sh`) — NEW failures = 0.
- **Live E2E:** real questions through the real path (grounded, partial, ungrounded) with verbatim captures.

---

## 8. Rollout / risk

- Phase 0 changes data → **dry-run + owner sign-off** before destructive steps; quarantine not delete; backups first.
- HTML render migration touches all send paths → done behind `render.py` with a plain-text fallback and tests.
- Expect the bot to **refuse more** until the corpus is fixed — this is correct and intentional; Phase 0 mitigates by increasing usable law first.
- Local-only invariant preserved (no cloud providers).

---

## 9. Open questions

1. Minimum content length to count as a "usable source" (proposed: ≥400 chars of real text).
2. Relevance threshold for GROUNDED vs PARTIAL (proposed: start simple — any stage-1/2 hit = GROUNDED; stage-3/4 = PARTIAL; none = UNGROUNDED).
3. Whether UNGROUNDED should ever offer a *general, explicitly non-authoritative* explanation (owner has said no — confirm).
4. Which sources in `sources.json` are authoritative enough to keep.

---

## 10. Suggested implementation order

0. Corpus integrity (audit → remediate → validate → source hygiene)
1. Retrieval & strict grounding
2. Verifiable answers (quotes + citation guard + template)
3. Formatting/UX
4. Serve-users-better mining

Each phase ships independently with tests and evidence.
