import hmac
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
from core.build_manager import (
    create_build,
    list_builds,
    get_build,
    submit_answer,
    approve_architecture,
    start_generation,
)
from core.project_templates import TEMPLATES


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
        API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        API_TOKEN_PATH.parent.chmod(0o700)  # mkdir's mode is umask-affected; force it

        # Create with the final 0600 mode from the very first syscall -- no
        # window where the file exists with looser (e.g. default 0644)
        # permissions. O_EXCL also means this raises rather than silently
        # overwriting if another process won the race to create it first --
        # in that case just fall through and read what it wrote.
        try:
            fd = os.open(API_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(fd, secrets.token_urlsafe(32).encode())
            finally:
                os.close(fd)

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
    presented = authorization or ""

    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API token")

    return BRIDGE_OPERATOR


class ApprovalAction(BaseModel):
    note: str | None = None


class CreateBuildRequest(BaseModel):
    name: str
    description: str
    project_path: str
    template: str | None = None


class AnswerAction(BaseModel):
    answer: str


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


@app.get("/templates")
def templates_endpoint():
    return {name: {"label": t["label"]} for name, t in TEMPLATES.items()}


@app.post("/builds")
def create_build_endpoint(
    body: CreateBuildRequest,
    operator: str = Depends(require_bridge_token),
):
    try:
        return create_build(body.name, body.description, body.project_path, template=body.template)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/builds")
def builds_endpoint():
    return list_builds()


@app.get("/builds/{build_id}")
def build_endpoint(build_id: str):
    result = get_build(build_id)

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/answer")
def answer_build_endpoint(
    build_id: str,
    body: AnswerAction,
    operator: str = Depends(require_bridge_token),
):
    try:
        result = submit_answer(build_id, body.answer)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/approve-architecture")
def approve_architecture_endpoint(
    build_id: str,
    action: ApprovalAction = ApprovalAction(),
    operator: str = Depends(require_bridge_token),
):
    try:
        result = approve_architecture(build_id, operator=operator, note=action.note)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result


@app.post("/builds/{build_id}/generate")
def generate_build_endpoint(
    build_id: str,
    operator: str = Depends(require_bridge_token),
):
    try:
        result = start_generation(build_id)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Build not found")

    return result
