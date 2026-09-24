# Legal Brain 2.0 — Phase 5: Two-Pass Reasoning + Uncertainty Engine

**Date:** 2026-09-24
**Status:** Implemented (Tasks 1–2)
**Branch:** `runner-kai-2.0-20260918`
**Scope:** Turn a single grounded answer into an adversarial, self-auditing
reasoning result: an uncertainty engine plus an Advocate → Opponent → Judge
pipeline. Local-only model fabric; authorities only from retrieved sources.

---

## 1. Problem

A single grounded generation is still a single point of view. It can over-claim,
miss a contrary clause, or present one source as settled law. Phase 5 adds
honest uncertainty and a structured adversarial pass, without weakening the
strict grounding that Phases 1–4 established.

## 2. Task 1 — Uncertainty engine (`core/juris_kai/uncertainty.py`)

Every proposition is classified against the retrieved authorities alone:

| Status | Rule |
|--------|------|
| `settled` | ≥2 **distinct** supporting authorities agree |
| `probable` | exactly one authority (or an authority plus an unverifiable citation) |
| `disputed` | supporting authority **and** contrary/repealed authority both retrieved |
| `unresolved` | no supporting authority, or only contrary/repealed authority |
| `missing_facts` | the proposition depends on facts that were not supplied |

Guarantees:
- **single-source ⇒ at most `probable`** (a duplicate of the same authority is
  still one source);
- an **invented citation** (one matching no retrieved document) is reported and
  can never reach `settled`;
- no authority ⇒ `unresolved` (never a guess).

`classify(propositions, docs) -> [{proposition, status, authorities, contrary,
reason}]`. `confidence()` and `summarize()` provide bounded, conservative
aggregates used by the judge.

## 3. Task 2 — Three-pass reasoning

### 3.1 Prompts (`core/juris_kai/prompts_reasoning.py`)

One strict template per pass, sharing `build_grounded_prompt`'s conventions:
the model may use **only** the supplied sources, must quote them, and must say
plainly when they do not support a point. Sources are neutralized and fenced.

- **Advocate** — strongest source-backed argument.
- **Opponent** — counter-argument, contrary authority, exceptions, gaps.
- **Judge** — established / disputed / unresolved, in IRAC form.

Budgets live in `prompt.TASK_MAX_TOKENS`: `juris_advocate` 500,
`juris_opponent` 500, `juris_judge` 800.

### 3.2 Engine (`core/juris_kai/reasoning.py`)

- `advocate(query, docs)` → grounded argument + propositions + authorities.
- `oppose(query, docs, argument)` → also **actively retrieves** contrary terms
  (`except`, `shall not apply`, `notwithstanding`, `repealed`, `amended`,
  `does not apply`, `provided that`) through the existing client, and scans the
  source text for contrary clause heads.
- `judge(query, advocate, opponent)` → integrates the uncertainty engine.
  `disputed` is only for points touched by **adverse authority** (or a contrary
  clause); differing interpretation of the *same* authority is not a conflict
  of authority.
- Every pass runs through the citation firewall, so invented citations are
  stripped/flagged before they can reach the result.
- `run_deep(query)` orchestrates retrieve → advocate → oppose → judge and
  returns a structured result with per-pass latency and `degraded` flag.

### 3.3 Degradation

- No/ungrounded retrieval → honest `unresolved` judgement, **no model call**.
- Advocate failure → single grounded pass fallback (deterministic source list
  if the model is down).
- Opponent failure → judge still runs with an empty opponent.
- Judge failure → deterministic model-free judgement.
- A failed pass **never** yields a blank answer.

## 4. Testing (TDD)

- `tests/test_juris_uncertainty.py` — each rule, no false `settled` from one
  source, conflict ⇒ `disputed`, empty ⇒ `unresolved`, unsupported citation,
  missing facts, aggregation.
- `tests/test_juris_reasoning.py` — structure of each pass, opponent finds a
  planted contrary clause, contrary retrieval, judge dispute/unresolved, IRAC
  shape, authority-only (invented citation stripped), uncertainty integration,
  and graceful degradation (single-pass and total model outage).

## 5. Interfaces

- `run_deep(query)` is the entry point used by Task 3 surfaces.
- Result: `{query, docs, verdict, advocate, opponent, judge, authorities,
  uncertainty, degraded, latency}`.
- Judge: `{established, disputed, unresolved, authorities, confidence, irac}`.

## 6. Non-goals / invariants

- Local-only (VM104 GPU `qwen3-coder:kai`); no cloud, no CPU generation.
- Authority-only: every authority comes from retrieval.
- Advisory only; not legal advice.
