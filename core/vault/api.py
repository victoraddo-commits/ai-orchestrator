"""Unified Vault 2.0 — Secret Broker API (§17/§23).

Operator-facing management of short-lived secret leases. Never returns secret
values — only references. Mounted by core/api.py.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from core.vault.broker import Denied, SecretBroker, is_high_risk, issue_lease_token


def _require_operator(x_kai_session: str | None = Header(default=None),
                      authorization: str | None = Header(default=None)) -> str:
    """Bridge token or a valid Command Center session — else 401."""
    if authorization:
        return "bridge"
    if x_kai_session:
        from core import authz
        if authz._resolve_session(x_kai_session):
            return x_kai_session
    raise HTTPException(status_code=401, detail="operator session required")

vault_router = APIRouter(prefix="/vault", tags=["vault"])

_CAPS = "/opt/ai-orchestrator/config/vault_capabilities.json"


def _default_policy() -> dict:
    """Build a subject→path policy from the vault capability manifest."""
    try:
        with open(_CAPS) as fh:
            caps = json.load(fh).get("capabilities", {})
        paths = list(caps.get("read", [])) + list(caps.get("write", []))
    except Exception:  # noqa: BLE001
        paths = ["ai-orchestrator/providers/*", "secrets/*"]
    return {
        "operator": paths or ["*"],
        "opencode": ["ai-orchestrator/providers/*", "secrets/telegram/*"],
        "claude-code": ["ai-orchestrator/providers/*"],
    }


_BROKER = SecretBroker(_default_policy())


class IssueBody(BaseModel):
    subject: str
    path: str
    operations: list | None = None
    ttl: int = 300
    max_uses: int = 1


class LeaseBody(BaseModel):
    cap_id: str
    operation: str = "reveal"


class RevokeBody(BaseModel):
    cap_id: str


@vault_router.get("/broker/capabilities")
def broker_capabilities():
    return {"capabilities": [{"id": c.id, "subject": c.subject, "path": c.path,
                              "operations": list(c.operations),
                              "expires_at": c.expires_at, "used": c.used,
                              "max_uses": c.max_uses, "revoked": c.revoked}
                             for c in _BROKER.list()]}


@vault_router.get("/broker/audit")
def broker_audit(limit: int = 200):
    return {"audit": _BROKER.audit(limit)}


@vault_router.post("/broker/issue")
def broker_issue(body: IssueBody):
    try:
        cap = _BROKER.issue(body.subject, body.path,
                            operations=tuple(body.operations or ["reveal"]),
                            ttl=body.ttl, max_uses=body.max_uses)
    except Denied as e:
        return {"ok": False, "denied": str(e)}
    return {"ok": True, "capability": {"id": cap.id, "path": cap.path,
                                       "expires_at": cap.expires_at,
                                       "max_uses": cap.max_uses}}


@vault_router.post("/broker/lease")
def broker_lease(body: LeaseBody):
    try:
        return {"ok": True, **_BROKER.lease(body.cap_id, body.operation)}
    except Denied as e:
        return {"ok": False, "denied": str(e)}


@vault_router.post("/broker/revoke")
def broker_revoke(body: RevokeBody):
    return {"ok": _BROKER.revoke(body.cap_id)}


class HighRiskBody(BaseModel):
    subject: str
    path: str


@vault_router.post("/highrisk/authorize")
def highrisk_authorize(body: HighRiskBody, operator: str = Depends(_require_operator)):
    """Duo-approve a high-risk reveal, then mint a ``duo:1`` lease for the plane.

    The machine plane refuses high-risk paths without a Duo-approved lease, so
    this is the only way to reveal them.
    """
    if not is_high_risk(body.path):
        return {"high_risk": False, "message": "path is not high-risk; normal auth applies"}
    from core.auth import duo_sso
    from core.vault.duo import config_from_env
    approver = config_from_env().get("username") or body.subject
    ap = duo_sso.approve(approver, purpose=f"vault:{body.path}")
    if not ap.get("approved"):
        return {"high_risk": True, "approved": False, "detail": ap}
    try:
        with open("/etc/kai/vault_lease_key") as fh:
            key = fh.read().strip()
    except OSError:
        return {"high_risk": True, "approved": False, "error": "lease key not configured"}
    token = issue_lease_token(body.subject, body.path, key, ttl=300, duo=True)
    return {"high_risk": True, "approved": True, "lease": token, "ttl": 300}
