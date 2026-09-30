"""Universal Onboarding State Machine — the engine.

This module owns the *mechanics* of the state machine and nothing else:

* the canonical order (:data:`core.onboarding.schema.STATE_SEQUENCE`);
* which states apply to a provider (N/A states are skipped, never run);
* one :func:`step` that executes the handler for the current state, records
  evidence, and advances — returning the mutated record for the caller to
  persist.

All I/O (identity, accounts, provider adapter, browser, mail, SMS, human paging)
is reached through an injected :class:`Pipeline`; :mod:`core.onboarding.manager`
provides the real one and :mod:`tests.test_onboarding` provides fakes. Nothing
here stores a secret: evidence is references + content hashes only.

Failure model
-------------
A handler returns one of:

* ``{"evidence": {...}}``         → success; state is completed and the machine
  advances to the next applicable state (or COMPLETED).
* ``{"pause": True, ...}``        → the flow needs a human; the machine enters
  PAUSED_HUMAN, records exactly one pending action, and stops.
* ``{"fail": "<reason>"}``        → the flow fails; the machine enters FAILED.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from core.onboarding.schema import (
    STATE_SEQUENCE,
    OnboardingState,
    TERMINAL_STATES,
    PAUSED_STATE,
    now_iso,
)


def hash_text(text: str) -> str:
    """Stable content hash for an evidence entry (never reversible to a secret)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def hash_payload(payload) -> str:
    try:
        text = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return hash_text(text)


#: Which provider requirement makes a state applicable. States absent from this
#: map always apply.
STATE_REQUIREMENT: dict[OnboardingState, str] = {
    OnboardingState.EMAIL_VERIFICATION: "requires_email",
    OnboardingState.SMS_VERIFICATION: "requires_phone",
    OnboardingState.MFA: "requires_mfa",
    OnboardingState.IDENTITY_VERIFICATION: "requires_identity_verification",
}


def is_applicable(state: OnboardingState, descriptor) -> bool:
    """True when *state* applies to *descriptor* (else it is skipped as N/A)."""
    if state in (
        OnboardingState.CAPTCHA_HUMAN_TAKEOVER,
    ):
        return bool(
            getattr(descriptor, "requires_captcha", False)
            or getattr(descriptor, "requires_payment", False)
        )
    requirement = STATE_REQUIREMENT.get(state)
    if requirement is None:
        return True
    return bool(getattr(descriptor, requirement, False))


def next_applicable(state: OnboardingState, descriptor) -> Optional[OnboardingState]:
    """The next state after *state* that applies to *descriptor*.

    ``COMPLETED`` is always applicable, so the walk always terminates.
    """
    try:
        start = STATE_SEQUENCE.index(state)
    except ValueError:
        return OnboardingState.COMPLETED
    for candidate in STATE_SEQUENCE[start + 1:]:
        if candidate is OnboardingState.COMPLETED or is_applicable(candidate, descriptor):
            return candidate
    return OnboardingState.COMPLETED


def _human_action_for(descriptor) -> str:
    if getattr(descriptor, "requires_captcha", False):
        return "CAPTCHA"
    if getattr(descriptor, "requires_payment", False):
        return "PAYMENT"
    return "OTHER"


def _instruction(state: OnboardingState, descriptor, provider_id: str) -> str:
    name = getattr(descriptor, "display_name", provider_id)
    return {
        OnboardingState.MFA: f"Approve the multi-factor prompt for {name}.",
        OnboardingState.CAPTCHA_HUMAN_TAKEOVER: (
            f"Complete the CAPTCHA / payment step for {name} in the KAI browser."
        ),
        OnboardingState.IDENTITY_VERIFICATION: f"Complete the identity check for {name}.",
        OnboardingState.EMAIL_VERIFICATION: (
            f"Click the verification link {name} emailed to finish onboarding."
        ),
    }.get(state, f"A step needs you to continue onboarding with {name}.")


# ---------------------------------------------------------------------------
# step
# ---------------------------------------------------------------------------

def step(record: dict, descriptor, pipeline) -> dict:
    """Execute the handler for the current state and advance. Mutates *record*.

    The caller (:mod:`core.onboarding.manager`) persists the record after each
    call, so a crash mid-flow resumes from the last persisted state.
    """
    state = OnboardingState(record["current_state"])
    if state in TERMINAL_STATES or state is PAUSED_STATE:
        return record

    # N/A states are skipped, never executed.
    if not is_applicable(state, descriptor):
        if state.value not in record["skipped_states"]:
            record["skipped_states"].append(state.value)
        pipeline.obs.publish(
            "onboarding.state.skipped",
            {"mission_id": record["mission_id"], "state": state.value,
             "provider_id": record["provider_id"]},
        )
        _advance(record, descriptor)
        _touch(record)
        _maybe_emit_completion(record, pipeline)
        return record

    pipeline.obs.publish(
        "onboarding.state.entered",
        {"mission_id": record["mission_id"], "state": state.value,
         "provider_id": record["provider_id"]},
    )

    handler = _HANDLERS[state]
    outcome = handler(record, descriptor, pipeline)

    if "fail" in outcome:
        record["errors"].append({
            "state": state.value, "error": str(outcome["fail"]),
            "at": now_iso(), "retryable": bool(outcome.get("retryable", True)),
        })
        record["status"] = "failed"
        record["current_state"] = OnboardingState.FAILED.value
        pipeline.obs.publish(
            "onboarding.failed",
            {"mission_id": record["mission_id"], "state": state.value,
             "provider_id": record["provider_id"], "reason": str(outcome["fail"])},
            severity="important",
        )
        pipeline.obs.audit(
            "onboarding.failed",
            {"mission_id": record["mission_id"], "state": state.value,
             "reason": str(outcome["fail"])},
        )
        _touch(record)
        return record

    if outcome.get("pause"):
        record["status"] = "paused_human"
        record["human_action_required"] = True
        record["human_action_id"] = outcome.get("human_action_id")
        record["human_action_type"] = outcome.get("action_type")
        record["pre_pause_state"] = state.value
        record["current_state"] = PAUSED_STATE.value
        pipeline.obs.publish(
            "onboarding.paused",
            {"mission_id": record["mission_id"], "state": state.value,
             "provider_id": record["provider_id"],
             "action_type": outcome.get("action_type"),
             "human_action_id": outcome.get("human_action_id")},
            severity="important",
        )
        pipeline.obs.audit(
            "onboarding.paused",
            {"mission_id": record["mission_id"], "state": state.value,
             "action_type": outcome.get("action_type")},
        )
        _touch(record)
        return record

    # success
    if state.value not in record["completed_states"]:
        record["completed_states"].append(state.value)
    evidence = outcome.get("evidence")
    if evidence:
        entry = {"state": state.value, "at": now_iso(), **evidence}
        record["evidence"].append(entry)
        pipeline.obs.checkpoint(
            record["mission_id"],
            f"onboarding {record['provider_id']}: {state.value} complete",
            {"state": state.value, "kind": evidence.get("kind")},
        )
    pipeline.obs.publish(
        "onboarding.state.completed",
        {"mission_id": record["mission_id"], "state": state.value,
         "provider_id": record["provider_id"]},
    )
    pipeline.obs.audit(
        "onboarding.state.completed",
        {"mission_id": record["mission_id"], "state": state.value,
         "kind": (evidence or {}).get("kind")},
    )
    _advance(record, descriptor)
    _touch(record)
    _maybe_emit_completion(record, pipeline)
    return record


def _maybe_emit_completion(record: dict, pipeline) -> None:
    if record.get("current_state") == OnboardingState.COMPLETED.value and \
            record.get("status") == "completed":
        pipeline.obs.publish(
            "onboarding.completed",
            {"mission_id": record["mission_id"], "provider_id": record["provider_id"],
             "account_id": record.get("account_id")},
        )
        pipeline.obs.audit(
            "onboarding.complete",
            {"mission_id": record["mission_id"], "provider_id": record["provider_id"],
             "account_id": record.get("account_id")},
        )


def _advance(record: dict, descriptor) -> None:
    jump = record.pop("_jump", None)
    state = OnboardingState(record["current_state"])
    if jump:
        target = OnboardingState(jump)
        _record_skips(record, state, target)
        record["current_state"] = target.value
        if target is OnboardingState.COMPLETED:
            _complete(record)
        return

    nxt = next_applicable(state, descriptor)
    _record_skips(record, state, nxt)
    record["current_state"] = nxt.value
    record["pending_state"] = None
    if nxt is OnboardingState.COMPLETED:
        _complete(record)


def _record_skips(record: dict, start: OnboardingState, end: OnboardingState) -> None:
    """Record the N/A states leapfrogged between *start* and *end*."""
    if start not in STATE_SEQUENCE or end not in STATE_SEQUENCE:
        return
    for skipped in STATE_SEQUENCE[STATE_SEQUENCE.index(start) + 1:
                                    STATE_SEQUENCE.index(end)]:
        if skipped is OnboardingState.COMPLETED:
            continue
        if skipped.value in record["completed_states"]:
            continue
        if skipped.value not in record["skipped_states"]:
            record["skipped_states"].append(skipped.value)


def _complete(record: dict) -> None:
    record["status"] = "completed"
    record["completed_at"] = now_iso()


def _touch(record: dict) -> None:
    record["updated_at"] = now_iso()


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

def _h_discover_provider(record, descriptor, p) -> dict:
    from core.providers import AutomationBlocked, HumanConfirmationRequired

    provider_id = record["provider_id"]
    try:
        p.provider.authorize(provider_id)          # blocking precondition, first
    except HumanConfirmationRequired as exc:
        action_id = p.human.request(
            "OTHER", record["mission_id"],
            instructions=(f"Confirm whether KAI may automate {provider_id}. "
                          f"{exc.decision.reason}"),
            provider=provider_id,
        )
        return {"pause": True, "action_type": "OTHER", "human_action_id": action_id}
    except AutomationBlocked as exc:
        return {"fail": f"automation_prohibited: {exc.decision.reason}"}
    except Exception as exc:  # noqa: BLE001 - unknown provider / registry error
        return {"fail": f"provider_error: {type(exc).__name__}"}

    return {"evidence": {
        "kind": "provider_descriptor",
        "hash": hash_payload(descriptor.model_dump()),
        "detail": {"automation_policy": descriptor.automation_policy.value,
                   "official_domain": descriptor.official_domain},
    }}


def _h_check_existing_account(record, descriptor, p) -> dict:
    provider_id = record["provider_id"]
    identity_hint = record.get("identity_id")
    if identity_hint:
        identity = p.identity.resolve(identity_hint)
        existing = p.accounts.find_existing(provider_id, identity) if identity else None
        if existing is not None:
            record["account_id"] = existing.account_id
            record["vault_reference"] = existing.vault_reference
            record["_jump"] = OnboardingState.END_TO_END_TEST.value
            return {"evidence": {"kind": "existing_account",
                                 "ref": existing.account_id}}
    return {"evidence": {"kind": "existing_account", "ref": "none"}}


def _h_select_identity(record, descriptor, p) -> dict:
    identity = p.identity.resolve(record.get("identity_id")) or p.identity.default()
    if identity is None:
        return {"fail": "no_identity_available"}
    record["identity_id"] = identity.identity_id
    return {"evidence": {
        "kind": "identity",
        "ref": identity.identity_id,
        "detail": {"vault_namespace": identity.vault_namespace},
    }}


def _h_select_provider_adapter(record, descriptor, p) -> dict:
    from core.providers import (
        AdapterNotFound,
        AutomationBlocked,
        HumanConfirmationRequired,
    )
    from core.providers import store as provider_store
    from core.providers.generic_web import build_generic_adapter

    provider_id = record["provider_id"]
    try:
        adapter = p.provider.get_adapter(provider_id)
    except HumanConfirmationRequired as exc:
        # The ToS/legality gate still applies here: an unevaluated provider must
        # never be silently automated just because it has no seeded adapter.
        action_id = p.human.request(
            "OTHER", record["mission_id"],
            instructions=(f"Confirm whether KAI may automate {provider_id}. "
                          f"{exc.decision.reason}"),
            provider=provider_id,
        )
        return {"pause": True, "action_type": "OTHER", "human_action_id": action_id}
    except AutomationBlocked as exc:
        return {"fail": f"automation_prohibited: {exc.decision.reason}"}
    except AdapterNotFound:
        # No exact/seeded adapter: synthesize and register the universal adapter
        # for this domain so later states resolve it without re-gating.
        domain = getattr(descriptor, "official_domain", None) or provider_id
        adapter = build_generic_adapter(domain, descriptor=descriptor)
        try:
            provider_store.register_adapter(adapter)
        except provider_store.DuplicateAdapter:
            adapter = provider_store.get_adapter(provider_id)
        except Exception as exc:  # noqa: BLE001 - registry failure is fatal
            return {"fail": f"no_adapter: {type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001
        return {"fail": f"no_adapter: {type(exc).__name__}"}

    return {"evidence": {
        "kind": "provider_adapter",
        "ref": type(adapter).__name__,
        "hash": hash_payload(descriptor.model_dump()),
        "detail": {"generic": type(adapter).__name__ == "GenericWebAdapter"},
    }}


def _h_check_requirements(record, descriptor, p) -> dict:
    requirements = {
        "email": bool(descriptor.requires_email),
        "phone": bool(descriptor.requires_phone),
        "captcha": bool(descriptor.requires_captcha),
        "mfa": bool(descriptor.requires_mfa),
        "kyc": bool(descriptor.requires_identity_verification),
        "payment": bool(descriptor.requires_payment),
    }
    record["requirements"] = requirements
    identity = p.identity.resolve(record.get("identity_id"))
    missing = []
    if requirements["email"] and not (identity and identity.email):
        missing.append("email")
    if requirements["phone"] and not (identity and identity.phone):
        missing.append("phone")
    if missing:
        return {"fail": "missing_identity_requirements:" + ",".join(missing)}
    return {"evidence": {"kind": "requirements", "detail": requirements}}


def _h_prepare_browser(record, descriptor, p) -> dict:
    profile = p.browser.create_profile(record["identity_id"], record["provider_id"])
    record["browser_profile"] = profile
    return {"evidence": {"kind": "browser_profile", "ref": profile}}


def _h_start_registration(record, descriptor, p) -> dict:
    identity = p.identity.resolve(record["identity_id"])
    account = p.accounts.ensure_account(record, descriptor, identity)
    record["account_id"] = account.account_id
    record["vault_reference"] = account.vault_reference
    session = p.browser.open_session(
        record["identity_id"], record["provider_id"], record["mission_id"])
    session_id = session.get("session_id") if isinstance(session, dict) else None
    record["browser_session_id"] = session_id
    response = p.provider.op(
        descriptor, "registration",
        identity_id=record["identity_id"], browser_session_id=session_id,
        mission_id=record["mission_id"], account_id=record.get("account_id"),
        objective=record.get("objective"))

    payload = response.get("result") if isinstance(response, dict) else None
    if not isinstance(payload, dict):
        payload = response if isinstance(response, dict) else {}

    # Honor a learn-then-pause signal from the adapter (a recipe was learned or
    # re-learned and needs operator review). A CAPTCHA / identity-verification
    # signal is left to its dedicated state below, so those takeovers still run.
    if payload.get("requires_human") and payload.get("action_type") in (None, "OTHER"):
        action_type = payload.get("action_type") or "OTHER"
        action_id = p.human.request(
            action_type, record["mission_id"],
            instructions=payload.get("instructions")
            or f"Review the learned recipe for {record['provider_id']}.",
            provider=record["provider_id"])
        return {"pause": True, "action_type": action_type,
                "human_action_id": action_id}

    return {"evidence": {
        "kind": "provider_registration",
        "ref": session_id,
        "hash": hash_payload(response),
        "detail": {"not_applicable": bool(response.get("not_applicable"))}
        if isinstance(response, dict) else {},
    }}


def _h_enter_information(record, descriptor, p) -> dict:
    response = p.provider.op(
        descriptor, "profile_setup",
        identity_id=record["identity_id"],
        browser_session_id=record.get("browser_session_id"))
    snapshot = p.browser.perform(record.get("browser_session_id"), "inspect", {})
    page = snapshot.get("snapshot", snapshot) if isinstance(snapshot, dict) else {}
    page_hash = hash_payload({
        "url": (page or {}).get("url"),
        "title": (page or {}).get("title"),
        "text": (page or {}).get("text"),
    })
    return {"evidence": {
        "kind": "page_state",
        "ref": record.get("browser_session_id"),
        "hash": page_hash,
        "detail": {"profile_response": bool(response is not None)},
    }}


def _h_email_verification(record, descriptor, p) -> dict:
    result = p.edge.verify_email(record, descriptor) or {}
    if result.get("verified"):
        record["verification_state"]["email"] = True
        return {"evidence": {
            "kind": "email_verification",
            "ref": result.get("email_id"),
            "detail": {"message_id": result.get("message_id"),
                       "link": result.get("link")},
        }}
    if result.get("awaiting", True):
        action_id = p.human.request(
            "OTHER", record["mission_id"],
            instructions=_instruction(OnboardingState.EMAIL_VERIFICATION,
                                      descriptor, record["provider_id"]),
            provider=record["provider_id"])
        return {"pause": True, "action_type": "OTHER", "human_action_id": action_id}
    return {"fail": result.get("reason", "email_verification_failed")}


def _h_sms_verification(record, descriptor, p) -> dict:
    # Exactly one pending CONNECT_PHONE_FOR_SMS request per mission (idempotent).
    action_id = p.human.request(
        "CONNECT_PHONE_FOR_SMS", record["mission_id"],
        instructions=(f"Connect your phone (KAI SMS Forwarder) so the code for "
                      f"{descriptor.display_name} can be relayed."),
        provider=record["provider_id"])
    result = p.edge.consume_sms(record, descriptor) or {}
    if result.get("verified") or result.get("otp_present"):
        p.human.complete(action_id, reason="otp_received")
        record["verification_state"]["sms"] = True
        return {"evidence": {
            "kind": "sms_verification",
            "ref": result.get("message_id"),
            "detail": {"otp_present": True},
        }}
    return {"pause": True, "action_type": "CONNECT_PHONE_FOR_SMS",
            "human_action_id": action_id}


def _h_human_takeover(record, descriptor, p, *, action_type: str) -> dict:
    session_id = record.get("browser_session_id")
    takeover = p.browser.pause(
        session_id, reason=_instruction(
            OnboardingState.CAPTCHA_HUMAN_TAKEOVER, descriptor, record["provider_id"]),
        action_required=action_type)
    takeover_id = takeover.get("takeover_id") if isinstance(takeover, dict) else None
    action_id = p.human.request(
        action_type, record["mission_id"],
        instructions=_instruction(OnboardingState.CAPTCHA_HUMAN_TAKEOVER,
                                  descriptor, record["provider_id"]),
        provider=record["provider_id"])
    resumed = p.browser.resume(session_id)
    if isinstance(resumed, dict) and resumed.get("resumed"):
        p.human.complete(action_id, reason="takeover_complete")
        return {"evidence": {"kind": "human_takeover", "ref": takeover_id,
                             "detail": {"action": action_type}}}
    return {"pause": True, "action_type": action_type, "human_action_id": action_id}


def _h_mfa(record, descriptor, p) -> dict:
    return _h_human_takeover(record, descriptor, p, action_type="MFA")


def _h_captcha_human_takeover(record, descriptor, p) -> dict:
    return _h_human_takeover(record, descriptor, p,
                             action_type=_human_action_for(descriptor))


def _h_identity_verification(record, descriptor, p) -> dict:
    return _h_human_takeover(record, descriptor, p, action_type="ID_VERIFICATION")


def _h_security_configuration(record, descriptor, p) -> dict:
    response = p.provider.op(
        descriptor, "security_setup",
        identity_id=record["identity_id"],
        browser_session_id=record.get("browser_session_id"))
    # A security-setup response may name a vault *path* for a seed/token; only
    # the path is ever recorded.
    result = response.get("result") if isinstance(response, dict) else None
    vault_path = None
    if isinstance(result, dict):
        vault_path = result.get("vault_reference") or result.get("vault_path")
    return {"evidence": {
        "kind": "security_configuration",
        "ref": vault_path,
        "hash": hash_payload(response),
    }}


def _h_account_verification(record, descriptor, p) -> dict:
    needed = []
    if descriptor.requires_email:
        needed.append("email")
    if descriptor.requires_phone:
        needed.append("sms")
    missing = [k for k in needed if not record["verification_state"].get(k)]
    if missing:
        return {"fail": "account_not_verified:" + ",".join(missing)}
    response = p.provider.op(descriptor, "account_status",
                             account_id=record.get("account_id"))
    return {"evidence": {
        "kind": "account_verification",
        "ref": record.get("account_id"),
        "hash": hash_payload(response),
    }}


def _h_account_registry(record, descriptor, p) -> dict:
    account = p.accounts.set_verified(
        record["account_id"], dict(record["verification_state"]),
        {"mission_id": record["mission_id"], "provider_id": record["provider_id"]})
    p.accounts.link_account(record, account)
    return {"evidence": {"kind": "account_registry", "ref": account.account_id,
                         "detail": {"verification_status": "VERIFIED"}}}


def _h_vault(record, descriptor, p) -> dict:
    account = p.accounts.get(record["account_id"])
    reference = account.vault_reference if account else record.get("vault_reference")
    record["vault_reference"] = reference
    return {"evidence": {"kind": "vault_reference", "ref": reference}}


def _h_end_to_end_test(record, descriptor, p) -> dict:
    account = p.accounts.get(record["account_id"])
    if account is None:
        return {"fail": "end_to_end_no_account"}
    if getattr(account.verification_status, "value", account.verification_status) != "VERIFIED":
        return {"fail": "end_to_end_account_not_verified"}
    if record.get("browser_session_id"):
        try:
            p.browser.end(record["browser_session_id"])
        except Exception:  # noqa: BLE001 - session already gone is not fatal
            pass
    return {"evidence": {
        "kind": "end_to_end",
        "ref": account.account_id,
        "detail": {"verification_status": "VERIFIED",
                   "vault_reference": account.vault_reference},
    }}


_HANDLERS = {
    OnboardingState.DISCOVER_PROVIDER: _h_discover_provider,
    OnboardingState.CHECK_EXISTING_ACCOUNT: _h_check_existing_account,
    OnboardingState.SELECT_IDENTITY: _h_select_identity,
    OnboardingState.SELECT_PROVIDER_ADAPTER: _h_select_provider_adapter,
    OnboardingState.CHECK_REQUIREMENTS: _h_check_requirements,
    OnboardingState.PREPARE_BROWSER: _h_prepare_browser,
    OnboardingState.START_REGISTRATION: _h_start_registration,
    OnboardingState.ENTER_INFORMATION: _h_enter_information,
    OnboardingState.EMAIL_VERIFICATION: _h_email_verification,
    OnboardingState.SMS_VERIFICATION: _h_sms_verification,
    OnboardingState.MFA: _h_mfa,
    OnboardingState.CAPTCHA_HUMAN_TAKEOVER: _h_captcha_human_takeover,
    OnboardingState.IDENTITY_VERIFICATION: _h_identity_verification,
    OnboardingState.SECURITY_CONFIGURATION: _h_security_configuration,
    OnboardingState.ACCOUNT_VERIFICATION: _h_account_verification,
    OnboardingState.ACCOUNT_REGISTRY: _h_account_registry,
    OnboardingState.VAULT: _h_vault,
    OnboardingState.END_TO_END_TEST: _h_end_to_end_test,
}


# ---------------------------------------------------------------------------
# drive
# ---------------------------------------------------------------------------

MAX_STEPS = 64


def drive(record: dict, descriptor, pipeline, persist) -> dict:
    """Run :func:`step` until the machine pauses or terminates.

    ``persist(record)`` is called after every transition so the flow is
    crash-safe and resumable; it must write the record atomically.
    """
    for _ in range(MAX_STEPS):
        state = OnboardingState(record["current_state"])
        if state in TERMINAL_STATES or state is PAUSED_STATE:
            break
        before = record["current_state"]
        step(record, descriptor, pipeline)
        persist(record)
        if record["current_state"] == before:
            break
    return record


__all__ = [
    "hash_text",
    "hash_payload",
    "STATE_REQUIREMENT",
    "is_applicable",
    "next_applicable",
    "step",
    "drive",
    "MAX_STEPS",
]
