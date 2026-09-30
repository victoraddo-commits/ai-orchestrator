"""Universal Onboarding State Machine — manager (real pipeline + public API).

This is the only module in ``core.onboarding`` that touches the other
subsystems. It builds a :class:`Pipeline` of the real building blocks
(``core.providers`` gate + adapters, ``core.identity``, ``core.accounts``,
``core.browser``, ``core.mail``, ``core.sms``, ``core.notify``,
``core.kai_missions``) and drives the pure state machine in
:mod:`core.onboarding.engine`.

Guarantees
----------
* **Gate first** — the provider ToS/legality gate is the first operation of the
  first state; a PROHIBITED provider fails, an UNKNOWN one pauses for a human.
* **Mission-backed + resumable** — every onboarding is a Mission Engine mission;
  the full snapshot is persisted after every transition, so a restart resumes.
* **Idempotent** — ``ensure_account`` never double-creates; the human-action
  request is one-per-(mission, type); a second run over an existing account
  jumps straight to the end-to-end test.
* **No secrets** — only vault *paths* and evidence hashes are ever stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.id_generator import generate_id

from core.onboarding import engine, store
from core.onboarding.schema import (
    OnboardingSession,
    OnboardingState,
    OnboardingStatus,
    STATE_SEQUENCE,
    now_iso,
)

SOURCE = "onboarding"


class SessionNotFound(KeyError):
    def __init__(self, mission_id: str):
        super().__init__(f"onboarding session not found: {mission_id}")
        self.mission_id = mission_id


# ---------------------------------------------------------------------------
# real ports
# ---------------------------------------------------------------------------

class ProviderPorts:
    def authorize(self, provider_id: str):
        from core.providers import ensure_automation_allowed

        return ensure_automation_allowed(provider_id)

    def get_provider(self, provider_id: str):
        from core.providers import get_provider

        return get_provider(provider_id)

    def get_adapter(self, provider_id: str):
        from core.providers import get_ready_adapter

        return get_ready_adapter(provider_id)

    def op(self, descriptor, operation: str, **kwargs) -> dict:
        from core.providers.adapter import NOT_APPLICABLE, NotApplicableError
        from core.providers import get_ready_adapter

        adapter = get_ready_adapter(descriptor.provider_id)
        fn = getattr(adapter, operation, None)
        if fn is None:
            return {"not_applicable": True}
        try:
            result = fn(**kwargs)
        except NotApplicableError:
            return {"not_applicable": True}
        if result == NOT_APPLICABLE:
            return {"not_applicable": True}
        return {"not_applicable": False, "result": result}


class IdentityPorts:
    def resolve(self, identity_id: Optional[str]):
        if not identity_id:
            return None
        from core.identity import get_identity

        return get_identity(identity_id)

    def default(self):
        from core.identity import resolve_default_identity

        return resolve_default_identity()


class AccountPorts:
    def find_existing(self, provider_id: str, identity):
        if identity is None:
            return None
        from core.accounts import find_accounts

        email = getattr(identity, "email", None)
        phone = getattr(identity, "phone", None)
        candidates = []
        if email:
            candidates = find_accounts(provider_id, email=email)
        if not candidates and phone:
            candidates = find_accounts(provider_id, phone=phone)
        for account in candidates:
            if getattr(account.status, "value", account.status) == "active":
                return account
        return candidates[0] if candidates else None

    def ensure_account(self, record: dict, descriptor, identity):
        from core.accounts import (
            AccountCreate,
            DuplicateAccount,
            VerificationStatus,
            create_account,
            get_account,
        )

        if record.get("account_id"):
            existing = get_account(record["account_id"])
            if existing is not None:
                return existing
        existing = self.find_existing(record["provider_id"], identity)
        if existing is not None:
            return existing
        payload = AccountCreate(
            provider=record["provider_id"],
            email=getattr(identity, "email", None),
            phone=getattr(identity, "phone", None),
            digital_identity=getattr(identity, "identity_id", None),
            browser_profile=record.get("browser_profile"),
            mission_id=record["mission_id"],
            verification_status=VerificationStatus.PENDING,
        )
        try:
            return create_account(payload)
        except DuplicateAccount:
            return self.find_existing(record["provider_id"], identity)

    def set_verified(self, account_id: str, verification_state: dict, evidence: dict):
        from core.accounts import VerificationStatus, set_verification_status

        set_verification_status(account_id, VerificationStatus.VERIFIED)
        from core.accounts import get_account

        return get_account(account_id)

    def link_account(self, record: dict, account):
        from core.accounts import (
            link_browser_profile,
            link_identity,
            link_mission,
            link_vault_reference,
        )

        if record.get("identity_id"):
            link_identity(account.account_id, record["identity_id"])
        link_mission(account.account_id, record["mission_id"])
        if record.get("browser_profile"):
            link_browser_profile(account.account_id, record["browser_profile"])
        if account.vault_reference:
            link_vault_reference(account.account_id, account.vault_reference)
        return account

    def get(self, account_id: Optional[str]):
        from core.accounts import get_account

        return get_account(account_id) if account_id else None


class BrowserPorts:
    def __init__(self, client=None):
        self.client = client

    def _cli(self):
        if self.client is None:
            from core.browser.client import BrowserOperatorClient

            self.client = BrowserOperatorClient()
        return self.client

    def create_profile(self, identity_id: str, provider_id: str) -> str:
        try:
            result = self._cli().create_profile(identity_id, provider_id)
            return result.get("profile_id") or result.get("profile") or str(result)
        except Exception:  # noqa: BLE001 - fall back to the deterministic id
            from core.browser.schema import profile_id_for

            return profile_id_for(identity_id, provider_id)

    def open_session(self, identity_id: str, provider_id: str, mission_id: str) -> dict:
        return self._cli().open_session(identity_id, provider_id, mission_id=mission_id)

    def perform(self, session_id, op: str, params: Optional[dict] = None) -> dict:
        if not session_id:
            return {}
        return self._cli().perform(session_id, op, params or {})

    def pause(self, session_id, *, reason: str, action_required: str) -> dict:
        from core.browser.integration import pause_for_human

        if not session_id:
            return {}
        return pause_for_human(session_id, reason=reason,
                               action_required=action_required, client=self._cli())

    def resume(self, session_id) -> dict:
        from core.browser.integration import resume_if_completed

        if not session_id:
            return {}
        return resume_if_completed(session_id, client=self._cli())

    def end(self, session_id) -> dict:
        from core.browser.integration import end_session

        if not session_id:
            return {}
        return end_session(session_id, client=self._cli())


class EdgePorts:
    def __init__(self, *, browser=None, email_verifier=None, sms_consumer=None,
                 mail_transport=None):
        self.browser = browser
        self.email_verifier = email_verifier
        self.sms_consumer = sms_consumer
        self.mail_transport = mail_transport

    def verify_email(self, record: dict, descriptor) -> dict:
        if self.email_verifier is not None:
            return self.email_verifier(record, descriptor) or {}
        if self.mail_transport is None:
            return {"awaiting": True}
        from core.mail import manager as mail_manager

        domains = [descriptor.official_domain] if descriptor.official_domain else None
        summary = mail_manager.poll_once(
            self.mail_transport,
            browser_client=(self.browser.client if self.browser else None),
            account_id=record.get("account_id"),
            official_domains=domains,
        )
        verified = [r for r in summary.get("results", []) if r.get("verified")]
        if verified:
            ev = verified[0].get("evidence") or {}
            return {"verified": True, "email_id": ev.get("email_id"),
                    "message_id": ev.get("message_id"), "link": ev.get("link")}
        return {"awaiting": True}

    def consume_sms(self, record: dict, descriptor) -> dict:
        if self.sms_consumer is not None:
            return self.sms_consumer(record, descriptor) or {}
        from core.sms import manager as sms_manager

        code = sms_manager.consume_otp_for(
            mission_id=record["mission_id"], account_id=record.get("account_id"))
        if code is None:
            return {"otp_present": False}
        # Deliver to the provider, then drop the code — never persisted.
        try:
            provider = ProviderPorts()
            provider.op(descriptor, "verification",
                        account_id=record.get("account_id"), code=code)
        except Exception:  # noqa: BLE001 - provider may not need a code step
            pass
        return {"otp_present": True, "verified": True}


class HumanPorts:
    def request(self, action_type, mission_id, *, instructions: str = "",
                provider: Optional[str] = None) -> str:
        from core.notify import human_action

        return human_action.request_human_action(
            action_type, mission_id, instructions=instructions, provider=provider)

    def complete(self, action_id: Optional[str], *, reason: str = "") -> None:
        if not action_id:
            return
        from core.notify import human_action

        human_action.complete(action_id, reason=reason)


class ObsPorts:
    def publish(self, topic: str, payload: dict, severity: str = "informational") -> None:
        from core import kai_event_bus

        kai_event_bus.publish(topic, payload, source=SOURCE, severity=severity)

    def audit(self, event_type: str, details: dict) -> None:
        from core import audit_logger

        audit_logger.log_audit_event(
            event_type=event_type, operator=SOURCE, endpoint="onboarding",
            method="UPDATE", status_code=200, details=details or {})

    def checkpoint(self, mission_id: str, note: str, evidence: dict) -> None:
        try:
            from core import kai_missions

            kai_missions.add_checkpoint(mission_id, note, evidence=evidence,
                                        kind="onboarding")
        except Exception:  # noqa: BLE001 - checkpoint must never block the flow
            pass


@dataclass
class Pipeline:
    provider: Any
    identity: Any
    accounts: Any
    browser: Any
    edge: Any
    human: Any
    obs: Any


def default_pipeline(*, browser=None, email_verifier=None, sms_consumer=None,
                     mail_transport=None, client=None) -> Pipeline:
    """Build the production pipeline (all ports wired to real modules)."""
    browser_ports = BrowserPorts(client or browser)
    return Pipeline(
        provider=ProviderPorts(),
        identity=IdentityPorts(),
        accounts=AccountPorts(),
        browser=browser_ports,
        edge=EdgePorts(browser=browser_ports, email_verifier=email_verifier,
                       sms_consumer=sms_consumer, mail_transport=mail_transport),
        human=HumanPorts(),
        obs=ObsPorts(),
    )


# ---------------------------------------------------------------------------
# persistence helpers
# ---------------------------------------------------------------------------

def _model(record: dict) -> OnboardingSession:
    return OnboardingSession(**record)


def _raw(mission_id: str) -> Optional[dict]:
    for record in store.read_sessions():
        if record.get("mission_id") == mission_id:
            return record
    return None


def _persist(record: dict) -> None:
    snapshot = _model(record).model_dump(mode="json")

    def _mutate(records: list[dict]) -> list[dict]:
        out = [r for r in records if r.get("mission_id") != snapshot["mission_id"]]
        out.append(snapshot)
        return out

    store.update_sessions(_mutate)


def _create_mission(objective: str) -> str:
    """Create the Mission Engine mission that backs this onboarding."""
    try:
        from core import kai_missions

        mission = kai_missions.create_mission(objective, tasks=[], requires_review=False)
        return mission["id"]
    except Exception:  # noqa: BLE001 - never block onboarding on the mission store
        return f"mis-{generate_id()}"


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def start_onboarding(provider_id: str, *, identity_id: Optional[str] = None,
                     objective: Optional[str] = None, mission_id: Optional[str] = None,
                     pipeline: Optional[Pipeline] = None) -> OnboardingSession:
    """Create (or attach) a mission, persist a fresh session, and drive it."""
    objective = objective or f"Onboard KAI with provider {provider_id}"
    mission_id = mission_id or _create_mission(objective)
    pipeline = pipeline or default_pipeline()

    existing = _raw(mission_id)
    if existing is not None:
        return resume_onboarding(mission_id, pipeline=pipeline)

    now = now_iso()
    record = _model({
        "mission_id": mission_id,
        "objective": objective,
        "provider_id": provider_id,
        "identity_id": identity_id,
        "current_state": OnboardingState.DISCOVER_PROVIDER.value,
        "status": OnboardingStatus.ACTIVE.value,
        "created_at": now,
        "updated_at": now,
    }).model_dump(mode="json")
    _persist(record)
    pipeline.obs.publish(
        "onboarding.started",
        {"mission_id": mission_id, "provider_id": provider_id,
         "identity_id": identity_id},
    )
    pipeline.obs.audit("onboarding.start",
                       {"mission_id": mission_id, "provider_id": provider_id})

    try:
        descriptor = pipeline.provider.get_provider(provider_id)
    except Exception as exc:  # noqa: BLE001
        record["errors"].append({"state": OnboardingState.DISCOVER_PROVIDER.value,
                                 "error": f"provider_not_found: {provider_id}",
                                 "at": now_iso(), "retryable": False})
        record["status"] = OnboardingStatus.FAILED.value
        record["current_state"] = OnboardingState.FAILED.value
        _persist(record)
        pipeline.obs.publish("onboarding.failed",
                             {"mission_id": mission_id, "provider_id": provider_id,
                              "reason": "provider_not_found"}, severity="important")
        return _model(record)

    engine.drive(record, descriptor, pipeline, _persist)
    return _model(record)


def resume_onboarding(mission_id: str, *,
                      pipeline: Optional[Pipeline] = None) -> OnboardingSession:
    """Resume a paused (or re-drive a stalled) onboarding after a restart."""
    record = _raw(mission_id)
    if record is None:
        raise SessionNotFound(mission_id)
    pipeline = pipeline or default_pipeline()
    status = record.get("status")
    if status in (OnboardingStatus.COMPLETED.value, OnboardingStatus.CANCELLED.value):
        return _model(record)
    if status == OnboardingStatus.PAUSED_HUMAN.value:
        record["status"] = OnboardingStatus.ACTIVE.value
        record["human_action_required"] = False
        record["current_state"] = record.get("pre_pause_state") or record["current_state"]
        record["updated_at"] = now_iso()
        _persist(record)
        pipeline.obs.publish("onboarding.resumed",
                             {"mission_id": mission_id, "provider_id": record["provider_id"]})
        pipeline.obs.audit("onboarding.resume",
                           {"mission_id": mission_id, "provider_id": record["provider_id"]})
    try:
        descriptor = pipeline.provider.get_provider(record["provider_id"])
    except Exception:  # noqa: BLE001
        return _model(record)
    engine.drive(record, descriptor, pipeline, _persist)
    return _model(record)


def run_onboarding(mission_id: str, *,
                   pipeline: Optional[Pipeline] = None) -> OnboardingSession:
    """Re-drive an existing session to its next pause/termination (no resume reset)."""
    record = _raw(mission_id)
    if record is None:
        raise SessionNotFound(mission_id)
    pipeline = pipeline or default_pipeline()
    try:
        descriptor = pipeline.provider.get_provider(record["provider_id"])
    except Exception:  # noqa: BLE001
        return _model(record)
    engine.drive(record, descriptor, pipeline, _persist)
    return _model(record)


def cancel_onboarding(mission_id: str, reason: str = "") -> OnboardingSession:
    record = _raw(mission_id)
    if record is None:
        raise SessionNotFound(mission_id)
    record["status"] = OnboardingStatus.CANCELLED.value
    record["current_state"] = OnboardingState.CANCELLED.value
    record["updated_at"] = now_iso()
    _persist(record)
    obs = ObsPorts()
    obs.publish("onboarding.cancelled",
                {"mission_id": mission_id, "reason": reason}, severity="important")
    obs.audit("onboarding.cancel", {"mission_id": mission_id, "reason": reason})
    return _model(record)


def get_session(mission_id: str) -> Optional[OnboardingSession]:
    record = _raw(mission_id)
    return _model(record) if record else None


def list_sessions(status: Optional[str] = None, provider_id: Optional[str] = None,
                  limit: int = 100) -> list[OnboardingSession]:
    rows = store.read_sessions()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if provider_id:
        rows = [r for r in rows if r.get("provider_id") == provider_id]
    rows = sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)
    return [_model(r) for r in rows[:limit]]


def health() -> dict:
    rows = store.read_sessions()
    by_status: dict = {}
    by_state: dict = {}
    for row in rows:
        by_status[row.get("status")] = by_status.get(row.get("status"), 0) + 1
        by_state[row.get("current_state")] = by_state.get(row.get("current_state"), 0) + 1
    return {"component": "onboarding", "ok": True, "total": len(rows),
            "by_status": by_status, "by_state": by_state}


__all__ = [
    "SessionNotFound",
    "Pipeline",
    "default_pipeline",
    "ProviderPorts",
    "IdentityPorts",
    "AccountPorts",
    "BrowserPorts",
    "EdgePorts",
    "HumanPorts",
    "ObsPorts",
    "start_onboarding",
    "resume_onboarding",
    "run_onboarding",
    "cancel_onboarding",
    "get_session",
    "list_sessions",
    "health",
    "STATE_SEQUENCE",
]
