import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

from core.health import analyze
from core.incident_manager import load_incidents
from core.decision_engine import load_decisions
from core.approval import load_requests, approve, reject
from core.remediation import load_remediations
from core.verification import load_verification_history
from core.learning import summarize
from core.memory import load
from core.lifecycle import InvalidTransition


app = FastAPI(title="AI Orchestrator Observability API")


API_TOKEN_PATH = Path(
    os.environ.get("AI_ORCHESTRATOR_API_TOKEN_PATH", str(Path.home() / ".ai-orchestrator" / "api_token"))
)

BRIDGE_OPERATOR = "cloudcli-plugin"


def _load_api_token():
    """Shared secret between core/api.py and the trusted caller (the CloudCLI
    plugin's server-side bridge, the only thing that should ever call the
    write endpoints below). Generated on first use; never derived from or
    trusted from client-supplied request data."""

    if not API_TOKEN_PATH.exists():
        API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        API_TOKEN_PATH.write_text(secrets.token_urlsafe(32))
        API_TOKEN_PATH.chmod(0o600)

    return API_TOKEN_PATH.read_text().strip()


_load_api_token()  # ensure the token file exists as soon as the API starts,
# not lazily on the first write request -- the plugin bridge needs to be
# able to read it before it ever makes that first call.


def require_bridge_token(authorization: str | None = Header(default=None)) -> str:
    """Verifies the caller presented the shared secret and returns the
    identity to record as the operator -- this is the ONLY source of
    operator identity for write endpoints; it is never read from the
    request body, so a caller cannot forge who performed an action."""

    expected = f"Bearer {_load_api_token()}"

    if authorization != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid API token")

    return BRIDGE_OPERATOR


class ApprovalAction(BaseModel):
    note: str | None = None


@app.get("/health")
def health():

    findings = analyze()

    status = "degraded" if any(f.get("severity") == "critical" for f in findings) else "ok"

    return {
        "status": status,
        "findings": findings,
        "last_scan": load("system_state.json").get("last_scan")
    }


@app.get("/incidents")
def incidents():
    return load_incidents()


@app.get("/decisions")
def decisions():
    return load_decisions()


@app.get("/approvals")
def approvals():
    return load_requests()


@app.get("/actions")
def actions():
    return load_remediations()


@app.get("/verifications")
def verifications():
    return load_verification_history()


@app.get("/learning")
def learning():
    return summarize()


@app.post("/approvals/{request_id}/approve")
def approve_request(
    request_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(require_bridge_token),
):

    try:
        result = approve(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.post("/approvals/{request_id}/reject")
def reject_request(
    request_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(require_bridge_token),
):

    try:
        result = reject(request_id, note=action.note, operator=operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result
