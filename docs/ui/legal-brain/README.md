# Legal Brain panel — visual QA (Command Center)

Prepared 2026-09-24. Evidence for the new **Legal Brain** sidebar item
(`data-hash="legal"`) and `#panel-legal`, wired to `loadLegal()` in
`core/kai/command_center.html`.

## How it was captured

- Harness: `scripts/visual_qa_legal.mjs` (Playwright + Chromium 133).
- Target: `https://127.0.0.1:8000/command-center#legal` on LXC 111 (self-signed
  TLS, ignored), reached through a local SSH port-forward to the node.
- Auth: an operator session JWT minted on the host via
  `core.jwt_auth.create_jwt({"sub":"visual-qa","role":"operator"})` and injected
  into `localStorage` (`kai_command_session_v3`) — same mechanism the SPA uses.
- Widths: **360 / 768 / 1280** (`deviceScaleFactor: 1`, viewport height 900,
  `fullPage: true`).
- Every one of the six sub-tabs was clicked and captured: Ask, Reports, Gaps
  (Ask-to-Acquire), Everyday Law, Corpus health, Licences.

All 18 captures reported `ready=true`, `overflow=false`, no page errors.

## What was inspected

- No horizontal page scroll at any width (`documentElement.scrollWidth <=
  innerWidth`).
- Tab strip wraps cleanly; active tab is highlighted (`btn-accent`).
- Alignment/spacing rhythm, contrast of badges and metrics.
- Empty-state copy present in code for every list
  ("No reports yet", "No gaps recorded", "No topics.", "No register entries.");
  not triggered here because the corpus and queue are populated.
- Primary action obvious: **Ask** button is accent-highlighted; Gaps/Licences
  tables use a scrollable `.table-wrap`.

## Issues found and fixed

Applied to `core/kai/command_center.html` (5 string edits), then the API service
was restarted and the panel re-captured:

1. **Status/kind badges wrapped mid-word.** The global
   `overflow-wrap: anywhere` rule split `DEEP` into `DEE / P` (Reports table)
   and cramped other badges at narrow widths.
   Fix: added `white-space: nowrap` to `.badge`.
2. **Dense tables crushed at phone width.** Gaps / Reports / Licences tables
   (5–6 columns) squeezed instead of scrolling at 360.
   Fix: added `.table-wrap.wide table { min-width: 560px }` and marked those
   three tables `class="table-wrap wide"`.

Before/after evidence: `before/legal-reports-768.png` (badge split) vs
`after/legal-reports-768.png` (fixed).

## Console / network noise (not Legal Brain)

- `401 /api/susu/stats` — bridge-only endpoint; the SPA already degrades it to
  "stats unavailable in this session" (by design).
- `502 /cc/arbitra/arbitra/overview` — a different panel's prefetch.
- "SSL certificate error" on the service-worker script — expected with the
  self-signed cert in this harness.

None originate from the Legal Brain panel; `/api/legal/*` and
`/api/juris-kai/reports` all returned 200.

## Artifacts

- `after/legal-{ask,reports,gaps,everyday,health,licences}-{360,768,1280}.png`
- `before/legal-reports-768.png`
- `legal-report.json` — machine-readable harness result.
