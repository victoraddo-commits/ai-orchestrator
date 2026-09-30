# Universal Account Registration — Operator Runbook

**Scope:** how to run the first real account registration (Amazon, `customer`
type) against the capability built in STEPs 1–8. This is a **human-supervised**
runbook: KAI prepares and guides; the human performs every ToS/CAPTCHA/identity
/OTP/payment step. It does **not** enable, and must not be read as authorizing,
any autonomous provider registration.

**Hosts**

| Role | Where | Notes |
|---|---|---|
| Orchestrator + workers + capability code | CT111 `kai-orchestrator` (`192.168.1.111`), repo `/opt/ai-orchestrator`, venv `.venv` | API `ai-orchestrator-api` on `:8000` (HTTPS), SMS worker `kai-sms-worker` on `:8770`, mail worker `kai-mail-worker` |
| Browser operator (Playwright + virtual display) | CT110 `kai-browser` (`192.168.1.119`), service `kai-browser.service` on `:8090` | noVNC `http://192.168.1.119:6080/vnc.html` |
| Vault | CT107 `kai-vault` | referenced only by path |

Access from a workstation that can reach pve-B:

```bash
ssh -i /root/.ssh/pve2_deploy -o BatchMode=yes root@192.168.1.110
pct exec 111 -- bash -lc 'cd /opt/ai-orchestrator && .venv/bin/python ...'
```

---

## 1. The honest posture (read before anything)

Amazon's automation policy is recorded as **`UNKNOWN`**
(`core/providers/catalog.py::Amazon` descriptor; `policy_source` = Amazon
Conditions of Use, a paraphrase). The ToS/legality gate therefore **requires
explicit human confirmation before any automated path runs** — it does not infer
permission. Behaviour (`core/providers/gate.py`):

| Policy | Decision | Behaviour |
|---|---|---|
| `ALLOWED` | `allow` | automated path may run |
| `UNKNOWN` | `require_human_confirmation` | automated path is **blocked**; a human action is raised |
| `PROHIBITED` | `refuse_automation` | automated path refused; routed to human takeover |

`ensure_automation_allowed()` raises `HumanConfirmationRequired` for Amazon
until an operator records a policy decision. **KAI will not fabricate identity,
business, or financial data**, and will not click through a ToS it cannot read.

---

## 2. Prerequisites — what the operator must supply

1. **Real Digital Identity** (no fabrication). Create it once on CT111:
   ```bash
   cd /opt/ai-orchestrator
   .venv/bin/python - <<'PY'
   from core.identity import create_identity, IdentityCreate
   ident = create_identity(IdentityCreate(
       display_name="<legal name>",
       email="<real mailbox that KAI can read>",
       phone="+<E.164 real phone with the SIM in the forwarder>",
   ))
   print(ident.identity_id, ident.vault_namespace)
   PY
   ```
   Only `identity_id` (and the vault namespace) is persisted; credentials never
   live in the identity record (`core/identity/`).
2. **A phone with a SIM + the KAI SMS Forwarder**, on **Tailscale** and always
   on (see §8). The forwarder POSTs inbound SMS to the CT111 worker.
3. **A mailbox KAI can read** (mail worker, see §8 caveat) for the email OTP.
4. **A card on file** (Amazon requires it) — entered by a human.
5. **Presence/reachability** for the CAPTCHA / ToS takeover in the KAI browser.

---

## 3. Authorized run order (Amazon `customer`)

All API calls below are operator-gated: send a bridge token
(`Authorization: Bearer <token>`) or a valid CC operator session
(`X-Kai-Session: <token>`). The URL is `https://<ct111>:8000` (self-signed).

### Step 0 — confirm worker/browser health

```bash
# SMS worker
curl -s http://127.0.0.1:8770/health
# mail worker (module)
cd /opt/ai-orchestrator && .venv/bin/python -m core.mail health
# CT110 browser operator (no token on /health)
curl -s http://192.168.1.119:8090/health
```
Expect SMS `status: ok`, mail `ok: true`, browser `status: ok` +
`engine_available: true`.

### Step 1 — human decision on automation (the gate)

Starting onboarding for Amazon **pauses at `DISCOVER_PROVIDER`** with a human
action until the operator makes an explicit policy call. A **loosening** change
(UNKNOWN → ALLOWED) is routed through the approval queue and does **not** take
effect until the operator approves it; a **tightening** change applies
immediately (`core/providers/manager.py::set_policy_override`).

> Recommendation for the first run: keep the policy **UNKNOWN** and drive the
> registration as a guided, human-in-the-loop session (the operator performs
> the browser actions). Only loosen to `ALLOWED` if you have read the Amazon
> Conditions of Use yourself and accept the risk — and record the exact
> `policy_source` quote you relied on.

### Step 2 — start onboarding

```bash
curl -sk -X POST https://127.0.0.1:8000/api/onboarding \
  -H 'Authorization: Bearer <bridge-token>' -H 'Content-Type: application/json' \
  -d '{"provider":"amazon","account_type":"customer","identity_id":"<id>"}'
```
The response is a session summary. With policy `UNKNOWN` the session pauses and
a pending human action is created (`GET /api/notifications`).

### Step 3 — perform the human points (in order, as they pause)

Watch the Onboarding panel (or `GET /api/onboarding/{mission_id}`) for the
current state and its human-action banner. Each human action is completed with:

```bash
curl -sk -X POST \
  https://127.0.0.1:8000/api/onboarding/<mission_id>/human-action/<action_id>/complete \
  -H 'Authorization: Bearer <bridge-token>' \
  -d '{"reason":"..."}'
```

The human points for an Amazon `customer`:

| State | Action type | What the human does |
|---|---|---|
| `DISCOVER_PROVIDER` | `OTHER` (ToS/authorization) | Accept/authorize the automation decision in §3.1 |
| `CAPTCHA/HUMAN_TAKEOVER` | `CAPTCHA` | Open the noVNC session and solve the CAPTCHA/ToS page |
| `EMAIL_VERIFICATION` | (mail auto) | Amazon emails the code; the mail worker consumes it *if* the mailbox is wired (§8) |
| `SMS_VERIFICATION` | `CONNECT_PHONE_FOR_SMS` | Ensure the SIM phone + forwarder are on Tailscale; the OTP is handed over **in memory only** and **never shown in the CC** |
| `MFA` (if Amazon asks) | `MFA` | Approve the prompt on the human's device |
| payment (card on file) | `PAYMENT` | Enter the card yourself; KAI never handles raw card data |
| `IDENTITY_VERIFICATION` (seller/business only) | `ID_VERIFICATION` | Complete KYC yourself; KAI never fabricates/handles ID documents |

The engine resumes the **same persisted browser session** after each takeover
(it pauses in `CAPTCHA/HUMAN_TAKEOVER` / `IDENTITY_VERIFICATION`, not inside
`START_REGISTRATION` — see `docs/amazon_adapter.md`).

### Step 4 — completion invariants

On success the session reaches `COMPLETED`, and (proven by
`tests/test_onboarding.py::test_registry_and_vault_reference_set_on_success`,
`::test_local_e2e_simulated_onboarding`,
`tests/test_amazon_adapter.py::test_registration_stores_password_as_vault_ref_and_detects_captcha`):

- the account is written to the registry as **`VERIFIED`**;
- the **generated password lives only in the Vault** at
  `secrets/accounts/amazon/<account_id>` — the account record stores a
  `vault_reference` **path only** (never the plaintext);
- no account row is created until the human confirms (Amazon policy UNKNOWN).

---

## 4. Vault & secret handling

- Amazon password: **generated**, written to the Vault, only the path returned.
  The plaintext never reaches the session, registry, evidence, or any return
  value (asserted by `test_amazon_adapter.py`).
- SMS OTP: detected, correlated, handed over **in memory only** (short TTL,
  single-use, zeroized on consume); persisted body is `[REDACTED]`, only
  `otp_present: true` is stored (`core/sms/otp.py`, `core/sms/store.py`).
- Credential fields are only filled after the browser host is validated against
  the official Amazon domains (`core/browser/security.assert_credential_target`).
- The CC API shapes every response through whitelist helpers; a cross-endpoint
  leak sweep asserts no OTP/password/raw email is returned
  (`tests/test_account_registration_api.py`).

Verify no plaintext in the capability stores:

```bash
cd /opt/ai-orchestrator
# capability-owned stores contain only vault refs / booleans / hashes:
for f in account_registry.json identity_registry.json onboarding_sessions.json \
         sms_lines.json sms_inbox.json; do
  [ -f memory/$f ] && echo "== $f ==" && grep -iE '"(password|otp|token|secret|card|cvv)"' memory/$f || true
done
```

---

## 5. Inspecting the evidence ledger

Each state transition appends an `EvidenceEntry` (`state`, `kind`, `at`, `ref`,
`hash`, `detail`) to the session. `ref` names an artifact (screenshot path, email
id, account id, takeover id); `hash` is a content hash — **neither carries a
secret value**.

- UI: Command Center → **Onboarding** → session → Details (evidence + errors).
- API: `GET /api/onboarding/{mission_id}` → `session.evidence`.
- Raw: `memory/onboarding_sessions.json` (schema envelope with `records`).
- Audit/events: `core.audit_logger` + the event bus (`human.action.*`,
  `sms.*`, `provider.policy.*`).

---

## 6. Resume after restart

State is durable, so a crash/restart does not lose the mission:

- **Onboarding sessions** persist in `memory/onboarding_sessions.json`
  (`core/onboarding/store.py`). Resume via
  `POST /api/onboarding/{mission_id}/resume` (or `resume_onboarding()`).
- **Browser takeovers** persist in `takeovers.json` *before* the operator is
  paged, so the paused mission can be recovered
  (`core/browser/takeover.py::resume_if_completed`).
- **Human actions** persist in `memory/notifications.json`; one open request per
  `(mission_id, action_type)`, auto-completing for SMS OTP
  (`core/notify/human_action.py`).

---

## 7. Reading the Command Center pages

Open the Command Center (`https://<ct111>:8000/command-center`) — four panels;
all four are wired to the sidebar, `PANEL_TITLES`, and `PANEL_LOADERS`
(`docs/COMMAND_CENTER_COVERAGE.md`, guarded by `tests/test_cc_contract.py`):

| Panel | Backend | Shows |
|---|---|---|
| **Onboarding** | `GET/POST /api/onboarding`, `…/{id}`, `…/resume`, `…/cancel`, `…/human-action/{id}/complete` | progress stepper (19-state canonical sequence), human-action banner with **"I've completed this"**, evidence + errors |
| **Accounts** | `GET /api/accounts`, `GET /api/accounts/{id}` | registry table, status badge, verification flags, vault-ref **PRESENT** indicator, masked contact, evidence timeline |
| **Providers** | `GET /api/providers` | descriptor cards with automation-policy badge (**UNKNOWN · human confirm** for Amazon) and the `policy_source` citation, requirements chips |
| **Inbox** | `GET /api/sms/inbox`, `GET /api/notifications` | redacted SMS activity (classification/from/to/time only — never the body/OTP) + human-action queue with one-click complete |

---

## 8. Troubleshooting

**SMS OTP never arrives / hand-off never completes**
- The phone running the KAI SMS Forwarder must be on **Tailscale and always on**
  so it can POST to the CT111 worker; the human-action text says exactly this
  (`core/notify/human_action.py`). If the phone sleeps, the OTP is missed.
- Confirm the worker: `curl -s http://127.0.0.1:8770/health` → `status: ok` and
  an increasing `inbox`.
- The webhook is bearer-token protected; the forwarder must send the CT107 vault
  token `secrets/sms/webhook_token` (or `KAI_SMS_WEBHOOK_TOKEN`).

**`00`-prefix / international normalization**
- Some gateways deliver `00…` instead of `+…`. `core/sms/normalize.py::to_e164`
  canonicalizes `+`, `00`, and US `011` prefixes and strips redundant leading
  zeros, so equal numbers compare equal (fixed in STEP 5;
  `tests/test_sms_e164.py`). If a number still mismatches, check the raw `to`
  field on the SMS in the Inbox.

**Mail worker caveats**
- The mail worker is running (`core.mail service`, 60 s poll) but currently
  **`enabled_accounts: 0`** — one account is configured
  (`memory/mail_accounts.json`, `127.0.0.1`) and it is `enabled: false`, so the
  worker polls nothing and **email OTP will not be ingested** until it is
  enabled. Re-register with `core.mail.manager.register_account(..., enabled=True)`
  using the same `account_id` (there is no CC route for this).
- The configured mailbox must actually be able to receive the identity's Amazon
  verification mail (or the identity email must forward into it).
- `memory/mail_accounts.json` and `sms_lines.json` hold **vault references
  only**; IMAP/SMTP hosts are `127.0.0.1`.

**Browser takeover stuck**
- Open noVNC (`http://192.168.1.119:6080/vnc.html`) and act in the live session;
  then complete the human action (`…/human-action/{id}/complete`). The engine
  resumes the same session. If CT110 restarted, takeovers are persisted and
  recoverable via `TakeoverStore`.

---

## 9. Remaining human gates for a real first run

1. **Authorization** — operator decision on the Amazon automation policy
   (approval-queue gated; keep UNKNOWN unless you accept the ToS risk).
2. **Identity** — real name/email/phone entered by the human (§2).
3. **ToS/CAPTCHA** — human takeover in the KAI browser.
4. **Email OTP** — mail worker must be enabled and the mailbox wired.
5. **SMS OTP** — phone + forwarder on Tailscale, always on.
6. **Card on file** — entered by a human.
7. **(seller/business only) KYC** — completed by the human.

## 10. Open decisions (operator call)

- **Stepper length**: the canonical `STATE_SEQUENCE` has **19** entries
  (18 forward states + `COMPLETED`; `core/onboarding/schema.py`), while the
  original directive list was **16**. Confirm which the CC stepper should show
  and whether the extra states are skipped or merged.
- **`account_type` persistence**: resolved by the Amazon adapter at start, but
  **not stored** on `OnboardingSession` (only the free-text `objective` is).
  Decide whether to add a first-class `account_type` field.
- **Region scope**: which Amazon storefront domains
  (`amazon.co.uk|ca|de|fr|es|it|com.au|in|co.jp`) are in scope for launch.
- **`policy_source` quotation**: the current Amazon citation is a **paraphrase**;
  a human should confirm the exact Conditions-of-Use wording before relaxing the
  policy (`docs/amazon_adapter.md`).
- **`associates` identity-verification rules** and **payment/card handling**.
- **Roadmap status**: `roadmap.json` phases `UAR` and `UAR-STEP8` remain
  **`proposed`** — left for operator approval. This runbook does not change them.
