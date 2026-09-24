# Legal Brain 2.0 — Phase 5 Tasks 1–2 evidence (2026-09-24)

Branch: `runner-kai-2.0-20260918` — `core/juris_kai/{uncertainty,reasoning,prompts_reasoning}.py`

## Deliverables

| Artifact | Purpose |
|----------|---------|
| `core/juris_kai/uncertainty.py` | Honest proposition classification (settled/probable/disputed/unresolved/missing_facts) |
| `core/juris_kai/prompts_reasoning.py` | Strict per-pass prompts (advocate/opponent/judge) |
| `core/juris_kai/reasoning.py` | Three-pass engine + `run_deep` orchestration + degradation |
| `tests/test_juris_uncertainty.py`, `tests/test_juris_reasoning.py` | TDD tests (29) |
| `scripts/legal_phase5_probe.py` | Live reproduction harness |

## Uncertainty rules (implemented + tested)

- `settled` — ≥2 distinct supporting authorities agree (duplicate = one source).
- `probable` — exactly one authority, or an authority plus an unverifiable
  citation (single-source ⇒ at most probable).
- `disputed` — supporting authority **and** contrary/repealed authority meet.
- `unresolved` — no supporting authority, or only contrary/repealed authority.
- `missing_facts` — the proposition depends on facts that were not supplied.
- An invented citation (matching no retrieved doc) is reported and cannot settle.

## Test counts (CT111)

| Run | Result |
|-----|--------|
| Baseline before change (6 juris suites) | 161 passed |
| Baseline core two (grounding + firewall) | 87 passed |
| New Phase 5 tests | 29 passed |
| Core 8 suites after change (161 + 29) | 190 passed, 3 warnings |
| Full juris set + root security | 261 passed, 1 pre-existing env failure¹ |

¹ `test_juris_kai_security.py::test_security_boundaries` fails on
`FileNotFoundError: /root/.ai-orchestrator/self-build-workspaces/...` — a missing
self-build workspace unrelated to this change (reproduces in isolation).

## Live 3-pass run (real CT100 corpus + VM104 `qwen3-coder:kai`)

Harness: `scripts/legal_phase5_probe.py` (local-only model, CT100 retrieval).

### Latency (3 passes), seconds

| Query | retrieve | advocate | opponent | judge | total |
|-------|---------:|---------:|---------:|------:|------:|
| rape | 1.03 | 11.04 | 16.99 | 13.88 | **42.94** |
| human rights enforcement in ghana | 0.63 | 9.51 | 17.03 | 17.23 | **44.40** |

### rape — GROUNDED, degraded=False

- Authorities: Criminal Offences Act, 1960 (Act 29); Promotion of Proper Human
  Sexual Rights… Bill 2021; Human Sexual Rights and Family Values Bill 2025.
- Advocate: "Rape is a criminal offence under Ghanaian law. Source: Criminal
  Offences Act, 1960 Act 29 (Revised), Chapter Six, Section 97."
- Opponent: "The definition of rape under Section 98 … does not explicitly
  include non-consensual sexual intercourse involving a child under sixteen,
  even though such acts are criminalised separately under Section 101 …
  creates a distinction between 'rape' and 'defilement'."
- Judge: confidence 0.21; established = definition of rape in Act 29;
  disputed = scope of s.98 re minors; unresolved = contested/unsupported
  sub-points. IRAC issue = whether non-consensual acts with a child under 16
  are "rape" under Ghanaian law.

### human rights enforcement in ghana — GROUNDED, degraded=False

- Authorities: Constitution 1992 (as amended 1996); Human Sexual Rights and
  Family Values Bill 2025; Interception of Postal Packets and
  Telecommunication Messages Act; + 2 adverse authorities found by the opponent
  (Promotion of Proper Human Sexual Rights… Bill 2021; Armed Forces Amendment
  Bill 2022).
- Opponent actively found adverse authorities (contrary authorities list).
- Judge: confidence 0.50; established = 8 (Chapter V rights, CHRAJ under
  Chapter XVIII, Directive Principles, Interception Act framework, …);
  disputed = 7 (limits on enforcement, the 2025 Bill's legal status);
  unresolved = 1.

## Prompts / orchestration

- Separate strict template per pass; sources neutralized + fenced; model may
  use ONLY supplied sources and must quote.
- Opponent retrieves contrary terms via the existing client
  (`except`, `shall not apply`, `notwithstanding`, `repealed`, `amended`,
  `does not apply`, `provided that`) and scans source text for clause heads.
- Judge integrates uncertainty; `disputed` only for points touched by adverse
  authority (differing interpretation of the same authority is not conflict).
- Citation firewall on every pass (no invented citations survive).
- Degradation: ungrounded ⇒ no model call; advocate failure ⇒ single grounded
  pass; opponent/judge failure ⇒ deterministic judgement; never blank.
