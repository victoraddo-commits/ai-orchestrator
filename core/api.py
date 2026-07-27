from fastapi import FastAPI

from core.health import analyze
from core.incident_manager import load_incidents
from core.decision_engine import load_decisions
from core.approval import load_requests
from core.remediation import load_remediations
from core.verification import load_verification_history
from core.learning import summarize
from core.memory import load


app = FastAPI(title="AI Orchestrator Observability API")


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
