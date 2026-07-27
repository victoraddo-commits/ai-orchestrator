from fastapi import FastAPI, HTTPException
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


class ApprovalAction(BaseModel):
    operator: str | None = None
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
def approve_request(request_id: str, action: ApprovalAction = ApprovalAction()):

    try:
        result = approve(request_id, note=action.note, operator=action.operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result


@app.post("/approvals/{request_id}/reject")
def reject_request(request_id: str, action: ApprovalAction = ApprovalAction()):

    try:
        result = reject(request_id, note=action.note, operator=action.operator)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))

    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return result
