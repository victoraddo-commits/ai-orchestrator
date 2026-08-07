# 17Y: Scoped Auto-Approval with Operator Veto Window

**Status:** Design spec (implementation deferred — requires operator decision)  
**Created:** 2026-08-07  
**Priority:** 40  
**Dependency:** 17T (Operator action audit trail)

## 1. Motivation

In the current build pipeline, every architecture approval requires a human operator
to explicitly approve via Telegram. This is the right default for safety, but for
low-risk changes (docs-only, test-only, template-confined), the round-trip latency
creates unnecessary friction.

The operator explicitly rejected a blanket bypass of the approval gate (Claude Code
should not approve on their behalf), but proposed a narrower alternative: a **scoped
fast-path with a human veto window**. This spec formalizes that alternative.

## 2. Design (per operator directive, 2026-08-02)

### 2.1 Scope

**Architecture-approval gate ONLY.** Deploy approval (the step that actually ships code)
stays fully manual, no timeout, no exceptions. Revisit deploy auto-approval only after
this is proven on the lower-stakes gate.

### 2.2 Eligibility

A build's proposed plan/files_changed must match a narrow, explicit allowlist:

**Always eligible (auto-approve candidate):**
- Docs-only changes (`*.md`, `docs/**`, `README`)
- Test-only changes (`tests/**`, `*.test.*`, test fixtures)
- Changes confined to a single app's own `project_path` under an already-approved template

**Always ineligible (fall back to manual):**
- `core/security.py`
- `core/action_policy.py`
- `.env` or any environment file
- Systemd unit files (`*.service`, `*.timer`)
- Docker compose files (`docker-compose*.yml`)
- Anything that would expand `ALLOWED_ACTIONS`
- Any file outside the allowlist disqualifies the ENTIRE build — silently falls back
  to fully manual approval. The operator sees a normal approval request, not an error.

**Eligibility is checked at two points:**
1. When the build enters `WAITING_FOR_ARCHITECTURE_APPROVAL`
2. When the veto window expires (re-check — build may have changed)

### 2.3 Veto Window

1. Build enters `WAITING_FOR_ARCHITECTURE_APPROVAL` with confirmed eligibility
2. Telegram notification sent (same as today — full plan text via `format_state_change`)
3. **Included in the notification:** "⏳ Auto-approval in 12h unless you veto. Reply with /reject <build-id> to block."
4. If the operator has NOT approved, rejected, or replied within the veto window (default: **12 hours**):
   - Re-check eligibility per 2.2
   - If still eligible, auto-approve
   - If no longer eligible (retries, plan changes), fall back to manual
5. The operator can veto at any time during the window: `/reject <build-id>` or replying
   to the Telegram message with "reject" or "no"

**Configurable values:**
```python
# In .env or a config file (NOT hardcoded)
AUTO_APPROVE_VETO_WINDOW_HOURS = 12    # Default 12 hours
AUTO_APPROVE_ENABLED = False           # Kill switch — starts OFF
```

### 2.4 Kill Switch

A single config flag disables the fast path entirely:

```python
# core/auto_approve.py
AUTO_APPROVE_ENABLED = os.environ.get("AUTO_APPROVE_ENABLED", "0") == "1"
```

Checked BEFORE eligibility (2.2) and veto window (2.3) ever apply. Independent of
autonomy level — even at autonomy level 5, if the kill switch is off, nothing is
auto-approved.

**Default: OFF.** The operator must explicitly enable it. Phase 17Y does NOT enable it.

### 2.5 Audit Integrity

Auto-approval must record a **distinct, unambiguous operator identity** — never the
operator's actual name or identity. The audit trail must never make it look like a
human reviewed something they didn't.

```python
AUTO_APPROVE_OPERATOR_ID = "system(auto-approved-after-veto-window)"
```

The approval record stores:
```json
{
  "build_id": "...",
  "gate": "architecture",
  "approved_by": "system(auto-approved-after-veto-window)",
  "approved_at": "2026-08-08T02:00:00Z",
  "veto_window_hours": 12,
  "eligibility_check": {
    "files_changed": ["docs/README.md", "tests/test_foo.py"],
    "allowlist_match": "docs_and_test_only",
    "rechecked_at": "2026-08-08T02:00:00Z"
  },
  "human_vetoed": false
}
```

This is visible in the audit log and clearly distinguishable from a human approval.

## 3. Implementation Plan (future phase, NOT 17Y)

17Y is design-only. Implementation is deferred:

| File | Purpose |
|------|---------|
| `core/auto_approve.py` | Eligibility checker, veto window timer, kill switch |
| `core/orchestrator_cycle.py` | Call auto-approve check during cycle (after stale approval check) |
| `core/telegram_bridge.py` | Add veto-window hint to approval notifications |
| `core/approval.py` | Support `approved_by` with system identity |
| `tests/test_auto_approve.py` | Eligibility rules, veto window expiration, kill switch, audit integrity |

### Eligibility checker pseudocode

```python
def is_eligible_for_auto_approve(build: dict) -> tuple[bool, str]:
    """Returns (eligible, reason)."""
    if not AUTO_APPROVE_ENABLED:
        return False, "kill_switch_off"

    files = build.get("files_changed", [])
    if not files:
        return False, "no_files_listed"

    # Allowlist checks
    DISQUALIFYING_PATTERNS = [
        "core/security.py", "core/action_policy.py",
        ".env", "*.service", "docker-compose*"
    ]

    for f in files:
        for pattern in DISQUALIFYING_PATTERNS:
            if fnmatch.fnmatch(f, pattern):
                return False, f"disqualified_by_{f}"

    # Positive match
    all_docs = all(f.endswith(".md") or f.startswith("docs/") or f.startswith("README") for f in files)
    if all_docs:
        return True, "docs_only"

    all_tests = all(f.startswith("tests/") or ".test." in f for f in files)
    if all_tests:
        return True, "tests_only"

    # Template-confined: all files under a single project_path that matches a known template
    # (template check deferred — requires template registry from 19W)

    return False, "no_allowlist_match"
```

### Veto window checker (called from orchestrator cycle)

```python
def check_auto_approve_veto_windows():
    """Called at the top of the orchestrator cycle. Iterates builds in
    WAITING_FOR_ARCHITECTURE_APPROVAL, checks if any have passed their
    veto window, and auto-approves eligible ones."""
    builds = load_builds()
    now = datetime.now(timezone.utc)

    for b in builds:
        if b.get("status") != "WAITING_FOR_ARCHITECTURE_APPROVAL":
            continue

        entered_at_str = b.get("status_changed_at") or b.get("created_at")
        if not entered_at_str:
            continue

        entered_at = datetime.fromisoformat(entered_at_str)
        elapsed = now - entered_at
        veto_seconds = AUTO_APPROVE_VETO_WINDOW_HOURS * 3600

        if elapsed.total_seconds() < veto_seconds:
            continue  # Still in veto window

        eligible, reason = is_eligible_for_auto_approve(b)
        if not eligible:
            info(f"auto-approve: build {b['id']} veto window expired but no longer eligible ({reason})")
            continue

        # Auto-approve
        from core.approval import approve
        approve(
            build_id=b["id"],
            gate="architecture",
            approved_by=AUTO_APPROVE_OPERATOR_ID,
            audit_note={
                "mechanism": "auto-approve-veto-window",
                "veto_window_hours": AUTO_APPROVE_VETO_WINDOW_HOURS,
                "eligibility_reason": reason,
            }
        )
        info(f"auto-approve: build {b['id']} approved after {veto_window}h veto window ({reason})")
```

## 4. Testing

| Test | What it verifies |
|------|-----------------|
| `test_kill_switch_off_disables_everything` | AUTO_APPROVE_ENABLED=False → no build eligible |
| `test_docs_only_is_eligible` | All files are `*.md` → eligible |
| `test_tests_only_is_eligible` | All files in `tests/` → eligible |
| `test_security_file_disqualifies` | `core/security.py` in files → not eligible |
| `test_mixed_files_disqualify` | One doc + one non-doc → not eligible |
| `test_veto_window_not_yet_expired` | 1 hour elapsed → no auto-approve |
| `test_veto_window_expired_auto_approves` | 13 hours elapsed → auto-approve triggers |
| `test_audit_identity_is_system_not_human` | approved_by = "system(auto-approved-after-veto-window)" |
| `test_deploy_approval_never_auto` | Deploy gate is NOT auto-approved, ever |
| `test_kill_switch_independent_of_autonomy` | Even at autonomy level 5, kill switch off → no auto |

## 5. Safety Analysis

### What could go wrong?

1. **Auto-approval of a malicious file disguised as docs.** A `.md` file with embedded
   script injection. *Mitigation:* The architecture approval gate reviews the BUILD PLAN,
   not the file content. The plan must still make sense. Plus the deploy gate is still
   manual.
2. **Operator away for 12h, auto-approval fires, deploys broken code.** *Mitigation:*
   Deploy approval stays manual. Auto-approval only clears architecture — the build moves
   to `ARCHITECTURE_APPROVED → GENERATING`, not `DEPLOYING`.
3. **Build retries during veto window add disqualifying files, but cache is stale.**
   *Mitigation:* Eligibility is re-checked at veto window expiry, not cached.
4. **Operator didn't see the Telegram notification with veto hint.** *Mitigation:*
   The notification text explicitly mentions the veto window. Plus the kill switch
   starts OFF — the operator must consciously enable this.

### Why architecture gate only?

| Gate | Action | Risk | Auto? |
|------|--------|------|-------|
| Architecture approval | Review the plan | Low — no code is built yet | Candidate |
| Deploy approval | Ship code to production | High — live impact | Never auto |

Architecture approval says "this plan makes sense." Deploy approval says "ship it."
The first is a design review; the second is a launch decision. Only design review
is a candidate for auto-approval with a veto window.

## 6. Open Questions

1. **12 hours too long? Too short?** Start conservative. Once proven, the operator
   can reduce to 4–6 hours or increase to 24 hours via config.
2. **Should nightly builds be eligible?** Phase runs that the operator explicitly
   scheduled might qualify for a shorter window (1 hour). Consider a per-build-type
   veto window in v2.
3. **Should the operator get a 1-hour warning before auto-approval?** Sending a
   second Telegram notification at T-1h would reduce the chance of the operator
   missing the veto. Recommended for v1.

## 7. Non-Goals (explicitly out of scope)

- Auto-approval of deploy gate (never, per operator directive)
- Claude Code deciding whether to promote this phase (that call is the operator's)
- Auto-rejection of any build
- Replacing the human approval UX — this is additive, not a replacement
