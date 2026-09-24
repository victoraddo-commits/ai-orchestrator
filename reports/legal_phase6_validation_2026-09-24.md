# Legal Brain 2.0 — Phase 6 final validation

**Date:** 2026-09-24
**Hosts:** CT 100 `kai-legal-brain` (`:8100`, corpus `data/legal_brain.db`), CT 111 `ai-orchestrator` (bot/AgentGuard).
**Scope:** Phase 6 Tasks 3–5 — self-monitoring, practice/research tools, live validation.
**Verdict:** All new tools run end-to-end on the live stack; every output is authority-only (or an honest refusal) and passes the AgentGuard output gate. No test regressions.

## 1. Tool validation (live stack, bounded)

| Tool | Result | Authority-only? | Firewall applied? | Latency |
|---|---|---|---|---|
| `contract_analysis` | 5 clauses → 4 obligations, 2 risks, 1 termination, 1 liability | n/a — user document (zero-trust workspace) | ✅ | 0.02 s |
| `authority_bundle` | 2 issues → 12 retrieved authorities (deduped) | ✅ retrieval only | ✅ | 6.89 s |
| `legal_chronology` | 3 dated events, each source-labelled | user-provided facts | ✅ | 0.00 s |
| `research:statute` | 5 enactments/instruments (Bill excluded) | ✅ retrieval only | ✅ | 0.93 s |
| `research:case_law` | honest "no reported judgments" message | ✅ (no invention) | ✅ | 0.90 s |
| `issue_matrix` | 3-row Issue/Law/Authority/Facts/Counterargument/Status table | ✅ retrieval + Deep reasoning | ✅ | 98.84 s |

`firewall applied` = a recording citation verifier was invoked by the AgentGuard
output gate for that call. Every tool routes its rendered output through
`LegalGuard.guard_output` (citation firewall + injection guard).

### Samples

**Contract Analysis (excerpt)**
```
*Contract Analysis — Sample supply agreement*
_Analysed in the zero-trust workspace; this document is not added to the knowledge base._
Clauses detected: 5
*Obligations* (4) … *Risks* (2) … *Termination* (1) … *Liabilities* (1)
```

**Authority Bundle (excerpt)**
```
*Issue 1:* breach of contract
  1. Sale of Goods Act, 1962 (Act 137). Revised Edition (2022) [CURRENT]
  2. Companies Act, 2019 (ACT 992) (2021) [UNKNOWN]
  5. Companies Bill, 2013 (2016) [PROPOSED]
```

**Statute mode (excerpt)**
```
1. Sale of Goods Act, 1962 (Act 137) — 2022 [CURRENT]
3. Contracts Act, 1960 (ACT 25) — 1960 [AMENDED]
5. Contracts (Amendment) Act, 2023 Act 1114 — 2024 [UNKNOWN]
```

**Case-law mode (full)**
```
⚖️ *Case-law mode*: the authoritative corpus currently holds no reported
judgments, so there is no case-law to ground an answer. I will not invent cases.
Try *Statute* mode, or ask about an Act, LI or the Constitution.
```

**Issue Matrix (header + first row)**
```
| Issue | Law | Authority | Facts | Counterargument | Status |
| A contract of sale is formed when … | Sale of Goods Act, 1962 (Act 137) |
  Sale of Goods Act, 1962 (Act 137) | A agreed to sell goods. |
  Contracts Act, 1960 (ACT 25); … | PROBABLE |
```

## 2. Health snapshot (`GET /legal/health`, token-gated)

```json
{
  "docs": 1445,
  "with_content": 1320,
  "temporal_counts": {"PROPOSED": 387, "CURRENT": 325, "AMENDED": 17,
                      "REPEALED": 1, "NOT_YET_IN_FORCE": 0, "HISTORICAL": 0,
                      "UNKNOWN": 715},
  "unknown_status": 715,
  "stale_days": 180,
  "stale_authorities": 0,
  "integrity": {"hash_mismatches": 593, "duplicates": 2,
                "checked": 1445, "meta_missing": 268},
  "suspect_docs": 1
}
```

- Live endpoint latency: **≈ 7.7 s** (bounded suspect scan); 401 without the token.
- Coverage by area and per-doc detail are in the JSON body.

## 3. Watcher alert sample

**Live read-only diff** (corpus vs `data/legal_change_snapshot.json`):
`0` new changes since the last snapshot (baseline already established; no alert).

**Format sample** — the watcher's own `format_alert()` over the live corpus
with an empty baseline (every document classified; bounded to 4):
```
⚖️ *LEGAL CHANGE ALERT*
_2026-09-24T10:44:52Z_

1. *LAW:* Land Act 2020 (Act 1036)
   *CHANGE:* NEW ACT
   *STATUS:* ACT
   *POTENTIAL EFFECT:* New statute enters the corpus; review the duties/rights it creates.
   *ACTION:* flag_for_review
…
…and 1441 more change(s) — see the run report.
```
Classification on the live corpus: `NEW_ACT=654`, `NEW_BILL=387`,
`NEW_INSTRUMENT=404`. Bills are always `PROPOSED — not yet law`.

## 4. Honest gaps

1. **Case-law is absent.** The corpus holds no reported judgments, so Case-law
   mode returns an honest "no judgment corpus" message. No case is invented.
   (Known Phase 6 non-goal: no judgment source.)
2. **Temporal `UNKNOWN` backlog = 715 / 1445 docs.** These have no
   enactment/effective/expiry date and no repeal/amendment evidence; the engine
   refuses to guess "in force". Reducing this needs metadata enrichment.
3. **Integrity debt.** `593` hash mismatches and `268` documents missing a
   sidecar `document_meta` row. The read-only check reports them for human
   remediation; it never deletes.
4. **One suspect document** (`id 1539`) flagged by the poisoning scan. The scan
   is a bounded heuristic (literal prefilter + windowed `injection.scan`
   confirmation), not an exhaustive proof — a clean report is not a guarantee.
5. **Stale-authority metric is a proxy.** `updated_at` tracks sidecar writes,
   not legal revalidation; 0 stale at 180 days is only as meaningful as that.
6. **`issue_matrix` / Deep reasoning is model-dependent and slow** (~99 s for
   three grounded passes). It degrades deterministically if the model is down.
7. **Contract Analysis is heuristic** keyword classification (clauses /
   obligations / risks / termination / liabilities) — advisory, not a legal
   review; the user document never enters the corpus.

## 5. Tests

| Repo | Suite | Result |
|---|---|---|
| CT 100 (`juris-legal-brain`) | `tests/` full | **714 passed** (baseline 698 + 16 new) |
| CT 111 (`ai-orchestrator`) | juris / agentguard / legal selection | **495 passed** (baseline 467 + 28 new) |

New CT 100 tests: `tests/test_health.py` (16).
New CT 111 tests: `tests/test_juris_tools.py` (15), `tests/test_juris_practice_tools_wiring.py` (13).

## 6. Commits

- CT 100 `juris-legal-brain` (`master`): `bde1a1d` feat(legal): legal-brain health monitoring + endpoint
- CT 111 `ai-orchestrator` (`runner-kai-2.0-20260918`): `c8e8d3e` feat(legal): weekly knowledge-health summary in harvest job
- CT 111 `ai-orchestrator` (`runner-kai-2.0-20260918`): `69aac5b` feat(juris): practice + research tools (contract, issue matrix, chronology, authority bundle)
