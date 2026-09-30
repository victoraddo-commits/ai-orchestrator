"""Command Center — Universal Account Registration API (roadmap STEP 8).

The read/write surface for the account-registration subsystem built in STEPs
1-7: the Account Registry (``core.accounts``), Digital Identity
(``core.identity``), the Provider Registry + ToS/legality gate
(``core.providers``), the onboarding state machine (``core.onboarding``), the
SMS/OTP worker (``core.sms``) and the human-action queue (``core.notify``).

Every route is operator-gated (bridge token, a valid CC operator session, or
the auth-proxy identity headers from a trusted peer) and returns **shaped,
redacted** payloads. No credential, vault value, OTP code or SMS body ever
leaves the API — the shaping helpers below whitelist fields explicitly rather
than passing stored records through.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

cc_account_router = APIRouter(tags=["command-center-accounts"])


def _req_op(request: Request) -> None:
    """Require an operator: bridge token, a valid CC session, or the identity
    headers injected by the auth proxy. Used to gate every read and write.

    The identity headers are honoured only from a trusted peer
    (``core.auth.trusted_proxy``) -- an untrusted client cannot forge them."""
    if request.headers.get("authorization"):
        return
    tok = request.headers.get("x-kai-session", "")
    if tok:
        from core import authz
        try:
            role = authz.resolve_role(tok)
        except Exception:  # noqa: BLE001
            role = None
        if role == "operator":
            return
        if role is not None:
            raise HTTPException(status_code=403,
                                detail="operator capability required")
    from core.auth.trusted_proxy import proxy_identity
    if proxy_identity(request, request.headers.get("x-kai-user"),
                      request.headers.get("x-kai-user-id")):
        return
    raise HTTPException(status_code=401, detail="operator session required")


# ---------------------------------------------------------------------------
# redaction / shaping helpers
# ---------------------------------------------------------------------------


def _mask_email(value: Optional[str]) -> Optional[str]:
    if not value or "@" not in value:
        return None
    local, _, domain = value.partition("@")
    head = local[:1] if local else "*"
    return f"{head}***@{domain}"


def _mask_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) < 4:
        return "***"
    return "***" + digits[-4:]


def _vault_ref_hint(provider: Optional[str]) -> str:
    """A redacted namespace hint for a present vault reference (never the path)."""
    slug = (provider or "").strip().lower().replace(" ", "_").replace(".", "_")
    return f"secrets/accounts/{slug}/…" if slug else "secrets/accounts/…"


def _progress(record) -> dict:
    from core.onboarding.schema import STATE_SEQUENCE

    total = len(STATE_SEQUENCE)
    done = len(record.completed_states)
    return {
        "completed": done,
        "skipped": len(record.skipped_states),
        "total": total,
        "percent": round(100 * done / total) if total else 0,
    }


def _onboarding_summary(record) -> dict:
    return {
        "id": record.mission_id,
        "mission_id": record.mission_id,
        "objective": record.objective,
        "provider_id": record.provider_id,
        "identity_id": record.identity_id,
        "account_id": record.account_id,
        "current_state": record.current_state.value,
        "status": record.status.value,
        "human_action_required": record.human_action_required,
        "human_action_id": record.human_action_id,
        "human_action_type": record.human_action_type,
        "vault_ref_present": bool(record.vault_reference),
        "progress": _progress(record),
        "states": _state_plan(record),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
    }


def _state_plan(record) -> list[dict]:
    from core.onboarding.schema import STATE_SEQUENCE

    completed = set(record.completed_states)
    skipped = set(record.skipped_states)
    current = record.current_state.value
    pre_pause = record.pre_pause_state
    plan: list[dict] = []
    seen: set[str] = set()
    for state in STATE_SEQUENCE:
        value = state.value
        seen.add(value)
        if value == current:
            status = "current"
        elif value in completed:
            status = "completed"
        elif value in skipped:
            status = "skipped"
        elif value == pre_pause:
            status = "pending_human"
        else:
            status = "pending"
        plan.append({"state": value, "status": status})
    # Control states (PAUSED_HUMAN / FAILED / CANCELLED) are current but are not
    # in the linear happy path, so append them for an honest stepper.
    if current and current not in seen:
        plan.append({"state": current, "status": "current"})
    return plan


def _human_action_view(record: Optional[dict]) -> Optional[dict]:
    if not record:
        return None
    return {
        "action_id": record.get("action_id"),
        "action_type": record.get("action_type"),
        "mission_id": record.get("mission_id"),
        "provider": record.get("provider"),
        "status": record.get("status"),
        "instructions": record.get("instructions"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
    }


def _current_human_action(record) -> Optional[dict]:
    if not record.human_action_id:
        return None
    try:
        from core.notify import human_action

        raw = human_action.get(record.human_action_id)
    except Exception:  # noqa: BLE001 - surface the type even if the store fails
        raw = None
    if raw is None:
        return {
            "action_id": record.human_action_id,
            "action_type": record.human_action_type,
            "mission_id": record.mission_id,
            "provider": record.provider_id,
            "status": "unknown",
            "instructions": "",
            "created_at": None,
            "expires_at": None,
        }
    return _human_action_view(raw)


def _onboarding_detail(record, current_action: Optional[dict]) -> dict:
    detail = _onboarding_summary(record)
    detail.update({
        "requirements": dict(record.requirements or {}),
        "verification_state": dict(record.verification_state or {}),
        "evidence": [e.model_dump(mode="json") for e in record.evidence],
        "errors": [e.model_dump(mode="json") for e in record.errors],
        "human_action": current_action,
    })
    return detail


def _account_view(record) -> dict:
    return {
        "account_id": record.account_id,
        "provider": record.provider,
        "account_type": record.account_type,
        "username": record.username,
        "email_masked": _mask_email(record.email),
        "phone_masked": _mask_phone(record.phone),
        "status": record.status.value,
        "verification_status": record.verification_status.value,
        "security_status": record.security_status.value,
        "risk_level": record.risk_level.value,
        "mission_id": record.mission_id,
        "digital_identity": record.digital_identity,
        "vault_ref_present": bool(record.vault_reference),
        "vault_ref_hint": _vault_ref_hint(record.provider) if record.vault_reference else None,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_verified": record.last_verified,
        "last_activity": record.last_activity,
    }


def _account_detail(record) -> dict:
    detail = _account_view(record)
    evidence: list[dict] = []
    errors: list[dict] = []
    try:
        from core.onboarding import list_sessions

        for session in list_sessions(limit=500):
            if session.account_id == record.account_id:
                evidence.extend(e.model_dump(mode="json") for e in session.evidence)
                errors.extend(e.model_dump(mode="json") for e in session.errors)
    except Exception:  # noqa: BLE001 - evidence is additive, never fatal
        pass
    detail["evidence"] = evidence
    detail["errors"] = errors
    return detail


def _provider_view(descriptor) -> dict:
    policy = descriptor.automation_policy.value
    requires_human = policy != "ALLOWED"
    adapter_registered = False
    try:
        from core.providers import get_adapter

        get_adapter(descriptor.provider_id)
        adapter_registered = True
    except Exception:  # noqa: BLE001 - absence of an adapter is not an error
        adapter_registered = False
    if adapter_registered and policy == "ALLOWED":
        readiness = "ready"
    elif requires_human:
        readiness = "human_required"
    else:
        readiness = "unavailable"
    return {
        "provider_id": descriptor.provider_id,
        "display_name": descriptor.display_name,
        "official_domain": descriptor.official_domain,
        "registration_url": descriptor.registration_url,
        "account_types": list(descriptor.account_types),
        "requirements": {
            "email": descriptor.requires_email,
            "phone": descriptor.requires_phone,
            "captcha": descriptor.requires_captcha,
            "mfa": descriptor.requires_mfa,
            "kyc": descriptor.requires_identity_verification,
            "payment": descriptor.requires_payment,
        },
        "has_api": descriptor.has_api,
        "has_oauth": descriptor.has_oauth,
        "browser_required": descriptor.browser_required,
        "automation_policy": policy,
        "policy_source": descriptor.policy_source,
        "regions": list(descriptor.regions),
        "notes": descriptor.notes,
        "adapter_registered": adapter_registered,
        "requires_human": requires_human,
        "readiness": readiness,
    }


def _sms_view(record) -> dict:
    """SMS metadata only. The body and any OTP code never leave the worker."""
    return {
        "message_id": record.message_id,
        "from": record.from_number,
        "to": record.to_number,
        "line": record.line,
        "classification": record.classification.value,
        "carrier": record.carrier.value,
        "sender_kind": record.sender_kind.value,
        "otp_present": record.otp_present,
        "suspicious": record.suspicious,
        "suspicious_reasons": list(record.suspicious_reasons),
        "mission_id": record.mission_id,
        "account_id": record.account_id,
        "received_at": record.received_at,
        "redacted": True,
    }


def _notification_view(record: dict) -> dict:
    return {
        "action_id": record.get("action_id"),
        "action_type": record.get("action_type"),
        "mission_id": record.get("mission_id"),
        "provider": record.get("provider"),
        "status": record.get("status"),
        "instructions": record.get("instructions"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
    }


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------


class OnboardingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=100)
    account_type: Optional[str] = Field(default=None, max_length=50)
    identity_id: Optional[str] = Field(default=None, max_length=120)
    objective: Optional[str] = Field(default=None, max_length=500)


class OnboardingCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=300)


class HumanActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# onboarding routes
# ---------------------------------------------------------------------------


@cc_account_router.get("/api/onboarding")
def onboarding_list(status: Optional[str] = None, provider_id: Optional[str] = None,
                    limit: int = 100, _: None = Depends(_req_op)):
    """List onboarding sessions with state, provider, account and progress."""
    from core.onboarding import list_sessions

    try:
        sessions = list_sessions(status=status, provider_id=provider_id,
                                 limit=max(1, min(limit, 500)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"onboarding unavailable: {type(exc).__name__}")
    return {"ok": True, "sessions": [_onboarding_summary(s) for s in sessions],
            "count": len(sessions)}


@cc_account_router.get("/api/onboarding/{mission_id}")
def onboarding_detail(mission_id: str, _: None = Depends(_req_op)):
    """Full session detail: state plan, evidence ledger, errors, human action."""
    from core.onboarding import get_session

    record = get_session(mission_id)
    if record is None:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    return {"ok": True,
            "session": _onboarding_detail(record, _current_human_action(record))}


@cc_account_router.post("/api/onboarding")
def onboarding_start(body: OnboardingStart, _: None = Depends(_req_op)):
    """Start an onboarding run (provider, account_type, objective/identity)."""
    from core.onboarding import start_onboarding
    from core.providers import ProviderNotFound, discover

    try:
        descriptor = discover(body.provider)
    except ProviderNotFound:
        raise HTTPException(status_code=404,
                            detail=f"unknown provider: {body.provider!r}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail=f"invalid provider: {type(exc).__name__}")

    objective = body.objective
    if not objective:
        objective = f"Onboard KAI with provider {descriptor.provider_id}"
        if body.account_type:
            objective += f" (account_type: {body.account_type})"

    try:
        session = start_onboarding(descriptor.provider_id,
                                   identity_id=body.identity_id,
                                   objective=objective)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"onboarding failed to start: {type(exc).__name__}")
    return {"ok": True, "session": _onboarding_summary(session)}


@cc_account_router.post("/api/onboarding/{mission_id}/resume")
def onboarding_resume(mission_id: str, _: None = Depends(_req_op)):
    """Resume a paused onboarding session."""
    from core.onboarding import SessionNotFound, get_session, resume_onboarding

    if get_session(mission_id) is None:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    try:
        session = resume_onboarding(mission_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    return {"ok": True, "session": _onboarding_summary(session)}


@cc_account_router.post("/api/onboarding/{mission_id}/cancel")
def onboarding_cancel(mission_id: str, body: Optional[OnboardingCancel] = None,
                      _: None = Depends(_req_op)):
    """Cancel an onboarding session (terminal)."""
    from core.onboarding import SessionNotFound, cancel_onboarding, get_session

    if get_session(mission_id) is None:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    reason = (body.reason if body else None) or ""
    try:
        session = cancel_onboarding(mission_id, reason=reason)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    return {"ok": True, "session": _onboarding_summary(session)}


@cc_account_router.post(
    "/api/onboarding/{mission_id}/human-action/{action_id}/complete")
def onboarding_human_action_complete(
        mission_id: str, action_id: str,
        body: Optional[HumanActionComplete] = None,
        _: None = Depends(_req_op)):
    """Operator marks a CAPTCHA/OTP/ID/ToS action done; the engine resumes."""
    from core.onboarding import SessionNotFound, get_session, resume_onboarding

    record = get_session(mission_id)
    if record is None:
        raise HTTPException(status_code=404, detail="onboarding session not found")

    try:
        from core.notify import human_action

        action = human_action.get(action_id)
    except Exception:  # noqa: BLE001
        action = None
    if action is None:
        raise HTTPException(status_code=404, detail="human action not found")
    if record.human_action_id and record.human_action_id != action_id:
        raise HTTPException(status_code=400,
                            detail="action does not belong to this onboarding session")
    if action.get("mission_id") and action.get("mission_id") != mission_id:
        raise HTTPException(status_code=400,
                            detail="action is bound to a different mission")

    reason = (body.reason if body else None) or "operator_completed"
    human_action.complete(action_id, reason=reason)
    try:
        session = resume_onboarding(mission_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="onboarding session not found")
    return {"ok": True, "action_completed": action_id,
            "session": _onboarding_summary(session)}


# ---------------------------------------------------------------------------
# account registry routes
# ---------------------------------------------------------------------------


@cc_account_router.get("/api/accounts")
def accounts_list(provider: Optional[str] = None, status: Optional[str] = None,
                  limit: int = 100, offset: int = 0, _: None = Depends(_req_op)):
    """List registry accounts (vault ref as a redacted PRESENT indicator)."""
    from core.accounts import list_accounts

    try:
        rows = list_accounts(provider=provider, status=status,
                             limit=max(1, min(limit, 500)), offset=max(0, offset))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"account registry unavailable: {type(exc).__name__}")
    return {"ok": True, "accounts": [_account_view(r) for r in rows],
            "count": len(rows)}


@cc_account_router.get("/api/accounts/{account_id}")
def accounts_detail(account_id: str, _: None = Depends(_req_op)):
    """Account detail with the vault-ref indicator and evidence timeline."""
    from core.accounts import get_account

    record = get_account(account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True, "account": _account_detail(record)}


# ---------------------------------------------------------------------------
# provider registry route
# ---------------------------------------------------------------------------


@cc_account_router.get("/api/providers")
def providers_list(_: None = Depends(_req_op)):
    """Provider descriptors with the honest automation policy + citation."""
    from core.providers import list_providers

    try:
        descriptors = list_providers()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"provider registry unavailable: {type(exc).__name__}")
    views = [_provider_view(d) for d in descriptors]
    return {"ok": True, "providers": views, "count": len(views)}


# ---------------------------------------------------------------------------
# SMS inbox + human-action queue routes
# ---------------------------------------------------------------------------


@cc_account_router.get("/api/sms/inbox")
def sms_inbox(limit: int = 100, classification: Optional[str] = None,
              _: None = Depends(_req_op)):
    """Redacted SMS activity feed — classification, from/to, timestamp only."""
    from core.sms import list_sms
    from core.sms.schema import SmsClassification

    if classification is not None:
        valid = {c.value for c in SmsClassification}
        if classification not in valid:
            raise HTTPException(status_code=400,
                                detail=f"invalid classification: {classification!r}")
    try:
        rows = list_sms(limit=max(1, min(limit, 500)), classification=classification)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"sms worker unavailable: {type(exc).__name__}")
    rows = list(reversed(rows))
    return {"ok": True, "messages": [_sms_view(m) for m in rows],
            "count": len(rows)}


@cc_account_router.get("/api/notifications")
def notifications_queue(_: None = Depends(_req_op)):
    """The pending human-action queue (CAPTCHA / OTP / ID / ToS requests)."""
    try:
        from core.notify import human_action

        pending = human_action.list_pending()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"human-action queue unavailable: {type(exc).__name__}")
    return {"ok": True, "notifications": [_notification_view(a) for a in pending],
            "count": len(pending)}


# ---------------------------------------------------------------------------
# site-recipe viewer routes (universal website registration)
# ---------------------------------------------------------------------------


class RecipePublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Optional[int] = Field(default=None, ge=1)


def _assert_recipe_secret_free(payload: dict) -> None:
    """Defence in depth: a shaped recipe must never carry secret material.

    The schema rejects a non-symbolic ``value_source`` at the model edge and the
    store guard rejects secrets at the persistence boundary. This third check
    runs on the *response* path, so even a record written straight into the
    store file can never be echoed back to the operator."""
    from core.site_recipes.store import find_recipe_secret_fields

    found = find_recipe_secret_fields([payload])
    if found:
        raise HTTPException(status_code=500,
                            detail="recipe failed the secret guard")


def _recipe_view(recipe) -> dict:
    """Compact, secret-free recipe summary. Selectors/URLs/symbolic sources only."""
    view = {
        "domain": recipe.domain,
        "signup_url": recipe.signup_url,
        "flow_type": recipe.flow_type.value,
        "status": recipe.status.value,
        "source": recipe.source.value,
        "version": recipe.version,
        "confidence": recipe.confidence,
        "requirements": recipe.requirements.model_dump(),
        "verification_flow": recipe.verification_flow,
        "last_verified_at": recipe.last_verified_at,
        "field_count": len(recipe.fields),
        "step_count": len(recipe.steps),
    }
    _assert_recipe_secret_free(view)
    return view


def _recipe_detail(recipe) -> dict:
    detail = _recipe_view(recipe)
    detail["fields"] = [f.model_dump(mode="json") for f in recipe.fields]
    detail["steps"] = [s.model_dump(mode="json") for s in recipe.steps]
    detail["evidence_ref"] = recipe.evidence_ref
    detail["created_at"] = recipe.created_at
    detail["updated_at"] = recipe.updated_at
    _assert_recipe_secret_free(detail)
    return detail


@cc_account_router.get("/api/site-recipes")
def site_recipes_list(_: None = Depends(_req_op)):
    """List the latest stored Site Recipe per domain (reviewable signup flows)."""
    from core.site_recipes import store as recipe_store

    try:
        recipes = recipe_store.list_recipes()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"recipe store unavailable: {type(exc).__name__}")
    return {"ok": True, "recipes": [_recipe_view(r) for r in recipes],
            "count": len(recipes)}


@cc_account_router.get("/api/site-recipes/{domain}")
def site_recipes_detail(domain: str, _: None = Depends(_req_op)):
    """Full recipe detail: ordered steps and fields (still secret-free)."""
    from core.site_recipes import store as recipe_store

    recipe = recipe_store.get_recipe(domain)
    if recipe is None:
        raise HTTPException(status_code=404, detail="site recipe not found")
    return {"ok": True, "recipe": _recipe_detail(recipe)}


@cc_account_router.post("/api/site-recipes/{domain}/publish")
def site_recipes_publish(domain: str, body: Optional[RecipePublish] = None,
                         _: None = Depends(_req_op)):
    """Promote a recipe version to published (operator action, audited shape).

    The store resolves the target version *before* mutating, so an unknown
    version can never demote the currently published row."""
    from core.site_recipes import store as recipe_store
    from core.site_recipes.store import RecipeNotFound

    if recipe_store.get_recipe(domain) is None:
        raise HTTPException(status_code=404, detail="site recipe not found")
    version = body.version if body else None
    try:
        recipe = recipe_store.publish_recipe(domain, version=version)
    except RecipeNotFound:
        raise HTTPException(status_code=404,
                            detail="site recipe version not found")
    return {"ok": True, "recipe": _recipe_detail(recipe)}


__all__ = ["cc_account_router"]
