"""Phase 18A-ai: Kai OIDC callback routes.

Mounted at /auth/kai/* — handles the vault SSO callback, step-up flow,
userinfo, and logout.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, Cookie, status

from core.oidc_client import OIDCClient, OIDCError
from core import jwt_auth
from core import authz

# Singleton — reused across all requests
_oidc = OIDCClient()

router = APIRouter(prefix="/auth/kai", tags=["auth"])


# ---------------------------------------------------------------------------
# GET /auth/kai/start
# ---------------------------------------------------------------------------

@router.get("/start")
def auth_kai_start():
    """Redirect the browser to the vault authorization URL."""
    url, state = _oidc.get_authorization_url()
    return Response(
        status_code=302,
        headers={"Location": url, "X-OIDC-State": state},
    )


# ---------------------------------------------------------------------------
# GET /auth/kai/callback
# ---------------------------------------------------------------------------

@router.get("/callback")
def auth_kai_callback(code: str, state: str):
    """Exchange authorization code for a JWT session cookie."""
    if not _oidc.validate_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    try:
        vault_response = _oidc.exchange_code(code, state, state)
    except OIDCError as e:
        err_str = str(e)
        if "vault_unreachable" in err_str:
            raise HTTPException(
                status_code=503,
                detail={"error": "vault_unreachable", "fallback": "/auth/login"},
            )
        raise HTTPException(status_code=400, detail=err_str)

    user = vault_response["user"]
    vault_role = user.get("role", "auditor")
    orch_role = _oidc.map_role(vault_role)
    step_up_fresh = vault_response.get("step_up_fresh", False)

    jwt_payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": orch_role,
        "vault_role": vault_role,
        "step_up_fresh": step_up_fresh,
    }
    jwt_token = jwt_auth.create_jwt(jwt_payload)

    _oidc.send_audit_event(
        "login",
        user["id"],
        "user",
        "success",
        {"client_id": "ai-orchestrator", "username": user["username"]},
    )

    response = Response(
        status_code=302,
        headers={"Location": "/dashboard"},
    )
    response.set_cookie(
        key="kai_session",
        value=jwt_token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        max_age=60 * 60 * 24,
    )
    return response


# ---------------------------------------------------------------------------
# POST /auth/kai/step-up
# ---------------------------------------------------------------------------

@router.post("/step-up")
def auth_kai_step_up(kai_session: str | None = Cookie(default=None)):
    """Return a step-up redirect URL requiring an existing session."""
    if not kai_session:
        raise HTTPException(status_code=401, detail="Session required for step-up")

    claims = jwt_auth.verify_jwt(kai_session)
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    url, _ = _oidc.get_authorization_url()
    return {
        "step_up_required": True,
        "redirect_url": url,
        "callback_path": "/auth/kai/step-up-callback",
    }


# ---------------------------------------------------------------------------
# GET /auth/kai/step-up-callback
# ---------------------------------------------------------------------------

@router.get("/step-up-callback")
def auth_kai_step_up_callback(code: str, state: str):
    """Same as regular callback but marks the session as freshly step-upped."""
    if not _oidc.validate_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    try:
        vault_response = _oidc.exchange_code(code, state, state)
    except OIDCError as e:
        err_str = str(e)
        if "vault_unreachable" in err_str:
            raise HTTPException(
                status_code=503,
                detail={"error": "vault_unreachable", "fallback": "/auth/login"},
            )
        raise HTTPException(status_code=400, detail=err_str)

    user = vault_response["user"]
    vault_role = user.get("role", "auditor")
    orch_role = _oidc.map_role(vault_role)

    jwt_payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": orch_role,
        "vault_role": vault_role,
        "step_up_fresh": True,
    }
    jwt_token = jwt_auth.create_jwt(jwt_payload)

    _oidc.send_audit_event(
        "step_up",
        user["id"],
        "user",
        "success",
        {"client_id": "ai-orchestrator", "username": user["username"]},
    )

    response = Response(
        status_code=302,
        headers={"Location": "/dashboard"},
    )
    response.set_cookie(
        key="kai_session",
        value=jwt_token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        max_age=60 * 60 * 24,
    )
    return response


# ---------------------------------------------------------------------------
# GET /auth/kai/userinfo
# ---------------------------------------------------------------------------

@router.get("/userinfo")
def auth_kai_userinfo(kai_session: str | None = Cookie(default=None)):
    """Return the authenticated user's claims from the JWT cookie."""
    if not kai_session:
        raise HTTPException(status_code=401, detail="Session required")

    claims = jwt_auth.verify_jwt(kai_session)
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return {
        "username": claims.get("username"),
        "role": claims.get("role"),
        "vault_role": claims.get("vault_role"),
        "step_up_fresh": claims.get("step_up_fresh", False),
    }


# ---------------------------------------------------------------------------
# POST /auth/kai/logout
# ---------------------------------------------------------------------------

@router.post("/logout")
def auth_kai_logout(kai_session: str | None = Cookie(default=None)):
    """Invalidate the session JWT and clear the cookie."""
    actor_id = "unknown"
    if kai_session:
        # Decode without full verification to extract actor_id for audit
        claims = jwt_auth.verify_jwt(kai_session)
        if claims:
            actor_id = claims.get("sub", "unknown")
        try:
            authz.invalidate_session(kai_session)
        except Exception:
            # Invalidate may raise for non-JWT tokens — ignore
            pass

    _oidc.send_audit_event("logout", actor_id, "user", "success", {})

    response = Response(status_code=200, content='{"status":"logged_out"}')
    response.headers["Content-Type"] = "application/json"
    response.set_cookie(
        key="kai_session",
        value="",
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        max_age=0,
    )
    return response
