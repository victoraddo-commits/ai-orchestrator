# Amazon Provider Adapter (STEP 7)

The first **real** provider adapter for the KAI Universal Account Registration
capability. It conforms to the `ProviderAdapter` interface
(`core/providers/adapter.py`), is registered in the provider catalog, and is
driven by the Onboarding state machine (`core/onboarding`).

> **Safety posture (read first).** Amazon's automation policy is recorded as
> **UNKNOWN**. The ToS/legality gate therefore **requires human confirmation
> before any automated step**. KAI does **not** assume automation is permitted.
> There is **no live-Amazon run path enabled** in this step: the adapter is
> exercised only against offline fixtures, and a real run is a documented,
> gated human step.

## Files

| File | Role |
|---|---|
| `core/providers/amazon.py` | `AmazonAdapter` + account-type/descriptor helpers + `register_amazon()` |
| `core/providers/catalog.py` | Amazon `ProviderDescriptor` (UNKNOWN policy + `policy_source`) |
| `core/providers/__init__.py` | registers the adapter on import; exports `AmazonAdapter` |
| `core/providers/manager.py` | `reset_registry()` re-registers the Amazon adapter |
| `core/providers/store.py` | `effective()` now re-validates overrides (enum vs string fix) |
| `core/onboarding/engine.py` | `START_REGISTRATION` passes `account_id` + `objective` to the adapter |
| `core/ai/kai_vault_client.py` | additive `store_secret()` (Vault machine-plane write) |
| `tests/test_amazon_adapter.py` | 20 offline TDD tests (interface, gate, fixture E2E, account types) |
| `tests/fixtures/mail/verification_amazon.eml` | offline Amazon verification email fixture |

## Descriptor (honest requirements)

| Field | Value |
|---|---|
| `provider_id` | `amazon` |
| `official_domain` | `amazon.com` |
| region domains | `amazon.co.uk`, `amazon.ca`, `amazon.de`, `amazon.fr`, `amazon.es`, `amazon.it`, `amazon.com.au`, `amazon.in`, `amazon.co.jp` |
| `registration_url` | `https://www.amazon.com/ap/register` (per account type; see below) |
| `account_types` | `customer` (first/default), `business`, `seller`, `associates` |
| `has_api` / `has_oauth` | `false` / `false` |
| `browser_required` | `true` |
| `requires_email` / `requires_phone` | `true` / `true` |
| `requires_captcha` | `true` |
| `requires_mfa` | `false` (optional) |
| `requires_identity_verification` | `false` (customer) / `true` (seller, business) |
| `requires_payment` | `true` (card on file) |
| `automation_policy` | **`UNKNOWN`** |
| `policy_source` | Amazon Conditions of Use — `https://www.amazon.com/gp/help/customer/display.html?nodeId=508088` (paraphrase: the Conditions restrict automated access; no permission clause found → policy stays UNKNOWN) |

## Account types

`resolve_account_type(objective)` maps the onboarding objective/context to an
account type; default **`customer`**. Keywords detect `seller` (seller /
sell on amazon / seller central / FBA / merchant), `business` (Amazon Business /
B2B / wholesale), and `associates` (affiliate / referral).

- `customer` — browser signup, email OTP, SMS OTP, CAPTCHA human takeover.
- `seller` / `business` — the onboarding is detected and **gated harder**:
  `requires_identity_verification=True`, so the engine stops at the
  `IDENTITY_VERIFICATION` state, which is always a **human** step. Publishing
  for these types returns `requires_human=True` (not implemented in STEP 7).
- `associates` — `publishing` is gated to a human; identity rules as customer
  for now (open decision).

## Where each interface method is used

| Method | Behaviour |
|---|---|
| `discovery()` | returns the resolved descriptor view incl. `official_domains`, `account_types`, policy |
| `registration()` | navigate → fill name/email → generate a strong password and store it in the Vault (ref only) → fill passwords → detect CAPTCHA / account-created |
| `verification()` | enters a relayed OTP code into the page (code never echoed) |
| `authentication()` | navigates to the sign-in page; reports auth state / possible MFA |
| `security_setup()` | opens security settings; returns the account Vault reference |
| `profile_setup()` | syncs the display name; never fabricates an address |
| `recovery()` | reports recovery options; never persists values |
| `publishing()` | `NOT_APPLICABLE` for `customer`; human-gated otherwise |
| `account_status()` | inspects the session page → `active` / `pending` |

## Human takeover ownership

The adapter **detects** blocking states (CAPTCHA / OTP / ToS) and returns a
`requires_human`/`action_type` hint. The **onboarding engine** performs the
actual `browser.pause_for_human(...)` + `human_action.request(...)` in its
`CAPTCHA/HUMAN_TAKEOVER` and `IDENTITY_VERIFICATION` states. This is deliberate:
pausing inside `START_REGISTRATION` would re-open a fresh browser session on
resume, whereas the engine's takeover states resume the *same* persisted
session, and it avoids creating two takeovers for one blocking page.

## How to run (offline)

```bash
# on CT111
cd /opt/ai-orchestrator
.venv/bin/python -m pytest tests/test_amazon_adapter.py -q      # 20 tests
.venv/bin/python /tmp/amazon_fixture_demo.py                    # transcript (dev aid)
```

The suite uses a fixture "Amazon signup page" (`FixtureAmazonBrowser`), the
real mail worker with `FixtureTransport` + `verification_amazon.eml`, and the
real SMS/OTP worker with a synthetic message. No network, no live Amazon.

## What the operator must do (a real run)

1. **Confirm automation** for `amazon` (policy override → `ALLOWED`, routed
   through the approval queue). Until then the gate pauses at `DISCOVER_PROVIDER`.
2. Provide the **Digital Identity** (legal name, email, phone) — real data only.
3. Be present (or reachable) for the **CAPTCHA / ToS** takeover in the KAI
   browser, and for **SMS OTP** (phone connected to the KAI SMS forwarder).
4. For **seller/business**, complete **identity verification** themselves
   (KYC documents) — KAI never fabricates or handles identity documents
   automatically.
5. A **card on file** is required by Amazon; payment entry is a human step.

## Security

- The generated password is written to the Vault at
  `secrets/accounts/amazon/<account_id>`; only that **path** is returned,
  persisted, or emitted. Tests assert the plaintext never reaches the session,
  the registry, evidence, or any return value.
- Credential fields are filled only after the browser host is validated against
  the official Amazon domains (`core.browser.security.assert_credential_target`).
- No secret is logged; Vault writes never log values.
- The Account Registry stores a `vault_reference` path only.

## Open decisions

- Exact canonical Amazon `policy_source` wording/quote should be re-verified by
  a human before any relaxation of the policy; the current text is a paraphrase.
- Region rollout: which storefront domains are actually in scope for launch.
- `associates` identity-verification requirements.
- Payment/`payments` handling for the card-on-file requirement.
- Roadmap/phase status update left to the operator (per orchestrator house rules).
