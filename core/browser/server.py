"""CT110 HTTP surface for the browser operator (FastAPI).

Thin transport over :class:`~core.browser.manager.BrowserOperator`. Endpoints
are synchronous so Starlette runs them in a threadpool (Playwright sync API).
Auth is a shared bearer token (``KAI_BROWSER_TOKEN``); only the CT111
orchestrator should hold it. Every endpoint that mutates state is audit-logged
by the manager.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from core.browser.manager import (
    ApprovalRequired,
    BrowserOperator,
    BrowserOperatorError,
    SessionNotFound,
    TooManySessions,
)
from core.browser.operations import InvalidOperation
from core.browser.schema import AuthState
from core.browser.security import (
    CredentialTargetRefused,
    DomainMismatch,
    EvaluateRefused,
    RedirectBlocked,
    UnsafeUrl,
)


class OpenSessionBody(BaseModel):
    identity_id: str
    provider_id: str
    mission_id: Optional[str] = None
    headless: Optional[bool] = None


class CreateProfileBody(BaseModel):
    identity_id: str
    provider_id: str


class OpBody(BaseModel):
    op: str
    params: dict = {}


class PauseBody(BaseModel):
    reason: str
    action_required: str
    instructions: str = ""
    resume_condition: dict = {}
    expires_in: int = 900


class SetDomainBody(BaseModel):
    provider_id: str
    official_domain: Optional[str] = None


def create_app(operator: Optional[BrowserOperator] = None, token: Optional[str] = None) -> FastAPI:
    token = token if token is not None else os.environ.get("KAI_BROWSER_TOKEN")
    app = FastAPI(title="KAI Browser Operator", version="1.0.0")

    if operator is None:
        from core.browser.engine import PlaywrightEngine
        from core.browser.sinks import JsonlSink

        root = os.environ.get("KAI_BROWSER_DATA_DIR", "/opt/kai-browser/data")
        headless = os.environ.get("KAI_BROWSER_HEADLESS", "1") == "1"
        engine = PlaywrightEngine(headless=headless)
        operator = BrowserOperator(
            root,
            engine=engine,
            sink=JsonlSink(root),
            novnc_url=os.environ.get("KAI_BROWSER_NOVNC_URL") or None,
            max_concurrent=int(os.environ.get("KAI_BROWSER_MAX_SESSIONS", "2")),
            headless=headless,
        )
    app.state.operator = operator

    def require_token(authorization: Optional[str] = Header(default=None)) -> None:
        if not token:
            raise HTTPException(status_code=503, detail="service token not configured")
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.exception_handler(SessionNotFound)
    def _404(_req, exc):  # noqa: ANN001
        return _json(404, str(exc))

    @app.exception_handler(InvalidOperation)
    def _422(_req, exc):  # noqa: ANN001
        return _json(422, str(exc))

    @app.exception_handler(UnsafeUrl)
    def _400(_req, exc):  # noqa: ANN001
        return _json(400, str(exc))

    @app.exception_handler(DomainMismatch)
    def _403(_req, exc):  # noqa: ANN001
        return _json(403, str(exc))

    @app.exception_handler(CredentialTargetRefused)
    def _403c(_req, exc):  # noqa: ANN001
        return _json(403, str(exc))

    @app.exception_handler(RedirectBlocked)
    def _403r(_req, exc):  # noqa: ANN001
        return _json(403, str(exc))

    @app.exception_handler(EvaluateRefused)
    def _403e(_req, exc):  # noqa: ANN001
        return _json(403, str(exc))

    @app.exception_handler(TooManySessions)
    def _429(_req, exc):  # noqa: ANN001
        return _json(429, str(exc))

    @app.exception_handler(ApprovalRequired)
    def _409(_req, exc):  # noqa: ANN001
        return _json(409, str(exc))

    @app.exception_handler(BrowserOperatorError)
    def _400b(_req, exc):  # noqa: ANN001
        return _json(400, str(exc))

    # -- routes ----------------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "operator": operator.health()}

    @app.get("/novnc")
    def novnc() -> dict:
        return {"novnc_url": operator.novnc_url}

    @app.get("/profiles", dependencies=[Depends(require_token)])
    def list_profiles() -> dict:
        return {"profiles": [p.model_dump(mode="json") for p in operator.profiles.list()]}

    @app.post("/profiles", dependencies=[Depends(require_token)])
    def create_profile(body: CreateProfileBody) -> dict:
        profile = operator.profiles.get_or_create(body.identity_id, body.provider_id)
        return {"profile": profile.model_dump(mode="json")}

    @app.post("/providers/domain", dependencies=[Depends(require_token)])
    def set_domain(body: SetDomainBody) -> dict:
        operator.set_provider_domain(body.provider_id, body.official_domain)
        return {"ok": True, "provider_id": body.provider_id,
                "official_domain": operator.official_domain(body.provider_id)}

    @app.post("/sessions", dependencies=[Depends(require_token)])
    def open_session(body: OpenSessionBody) -> dict:
        state = operator.open_session(
            body.identity_id, body.provider_id,
            mission_id=body.mission_id, headless=body.headless)
        return {"session": state.model_dump(mode="json")}

    @app.get("/sessions/{session_id}", dependencies=[Depends(require_token)])
    def get_session(session_id: str) -> dict:
        state = operator.get_session(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        return {"session": state.model_dump(mode="json")}

    @app.post("/sessions/{session_id}/op", dependencies=[Depends(require_token)])
    def perform(session_id: str, body: OpBody) -> dict:
        return {"result": operator.perform(session_id, body.op, body.params)}

    @app.post("/sessions/{session_id}/pause", dependencies=[Depends(require_token)])
    def pause(session_id: str, body: PauseBody) -> dict:
        record = operator.pause_for_human(
            session_id, reason=body.reason, action_required=body.action_required,
            instructions=body.instructions, resume_condition=body.resume_condition,
            expires_in=body.expires_in)
        return {"takeover": record.model_dump(mode="json")}

    @app.get("/sessions/{session_id}/takeover", dependencies=[Depends(require_token)])
    def get_takeover(session_id: str) -> dict:
        state = operator.get_session(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        record = operator.takeovers.get(state.takeover_id) if state.takeover_id else None
        return {"takeover": record.model_dump(mode="json") if record else None}

    @app.post("/sessions/{session_id}/resume", dependencies=[Depends(require_token)])
    def resume(session_id: str) -> dict:
        return operator.resume_if_completed(session_id)

    @app.post("/sessions/{session_id}/end", dependencies=[Depends(require_token)])
    def end(session_id: str) -> dict:
        state = operator.end_session(session_id)
        return {"session": state.model_dump(mode="json")}

    @app.get("/takeovers", dependencies=[Depends(require_token)])
    def list_takeovers() -> dict:
        return {"takeovers": [t.model_dump(mode="json") for t in operator.list_takeovers()]}

    return app


def _json(status: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"detail": detail})
