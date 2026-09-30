# Universal Website Registration — Design Spec

## Summary
Generalize KAI's account-registration capability from hand-written per-site adapters to a universal, browser-driven system that can register an account on ANY website: a reasoning agent reads the live signup flow, drives it through the existing browser operator, and persists what it learns as a reviewable per-domain **Site Recipe** so subsequent runs are fast and reliable.

## Context / Existing system (Steps 1–9, all built)
- Identity + Account Registry (`core/identity`, `core/accounts`), Provider Registry + legality gate (`core/providers`), Browser/Web Operator on CT110 (`core/browser`), Email Worker (`core/mail`), SMS/OTP Worker (`core/sms`, real phone lines proven, E.164 normalized), Human-action notifier (`core/notify`), Onboarding State Machine (`core/onboarding`), Command Center pages (`core/cc_account_routes.py` + `command_center.html`), Vault refs, evidence ledger, Amazon adapter (`core/providers/amazon.py`, policy UNKNOWN → human confirmation).
- Current limitation: a "provider" is a hand-written adapter; this does not scale to arbitrary sites.

## Decision
Approach **A — Hybrid**: live reasoning + learned, reviewable Site Recipes. (C = recipe-first seeding; D = vision-autonomous fallback deferred.)

## Architecture
`onboarding engine (unchanged) → adapter resolution`
- specialized/seeded adapter (optional override, e.g. amazon)
- **GenericWebAdapter(domain)** — DEFAULT for any domain
  - `SiteRecipeStore` — `memory/site_recipes/<domain>.json`, versioned, reviewable
  - `RecipeLearner` — reasoning over the browser DOM/a11y tree → draft recipe
  - `CapabilityClassifier` — domain/URL → SiteProfile
- drives the CT110 browser operator + mail/sms/human-takeover (unchanged)

## Components
1. **SiteRecipe** (schema): domain, signup_url, flow_type `single_page|multi_step|sso_only|invite_only|unknown`, fields[], requirements{email,phone,captcha,mfa,payment,kyc,email_verify,phone_verify}, steps[], verification_flow, confidence, source `seeded|learned`, status `draft|published|stale`, version, last_verified_at, evidence_ref. MUST NOT contain secrets (selectors/URLs only).
2. **SiteRecipeStore**: versioned CRUD; `draft→published` promotion (reviewable, mirroring the provider store); lookup by domain.
3. **CapabilityClassifier** (`core/discovery`): URL → SiteProfile (registrable?, requirements, automation policy). Policy defaults **UNKNOWN → human confirmation**.
4. **GenericWebAdapter(ProviderAdapter)**: synthesizes its descriptor from the SiteProfile; maps operations to the browser operator; uses a published recipe if present, else learns a draft and pauses for review; marks recipes `stale` on drift and re-learns.
5. **RecipeLearner**: reasoning loop over the browser DOM → proposes recipe steps; `source=learned, status=draft`.
6. **Resolution**: `SELECT_PROVIDER_ADAPTER` resolves an exact/seeded adapter first, else `GenericWebAdapter(domain)`.

## Data Flow / State hardening
classify → gate (UNKNOWN→human confirmation) → resolve adapter → recipe lookup OR learn → drive browser → human takeovers (CAPTCHA/ToS/OTP/MFA/payment/KYC) → verify email + SMS → registry + vault ref → evidence ledger → COMPLETED → promote/refresh recipe.

## Error Handling
- Bot-hostile / unknown UI → human path (vision-autonomous option D deferred as future fallback).
- CAPTCHA / MFA / payment / KYC → human takeover.
- Selector drift → mark recipe `stale`, re-learn, pause.
- Idempotent + resumable (reuse the existing onboarding engine guarantees).

## Security
- Official-host credential guard; Vault refs only (never plaintext secrets).
- Recipe store contains no secrets — selectors/URLs/metadata only.
- Automation policy default UNKNOWN → human confirmation gate before any automated step.
- No CAPTCHA/anti-bot bypass.

## Testing
- Recipe schema/store unit tests; classifier fixtures; GenericWebAdapter across ≥3 distinct offline fixture shapes (single-page signup, multi-step wizard, SSO/invite-only); learn→draft→publish; drift detection; gate UNKNOWN→human; full offline E2E per shape → COMPLETED; no-secrets assertions; CC recipe-viewer API tests. TDD, 80%+.

## Non-Goals
- No live-site registration without explicit operator authorization.
- No CAPTCHA/anti-bot bypass.
- Vision-autonomous (option D) deferred to a later phase as a fallback.

## Open Decisions
- Seeded recipe set to ship initially (which well-known sites).
- Recipe confidence thresholds for auto-publish vs human review.
- Which CC surface hosts the recipe viewer/editor.
