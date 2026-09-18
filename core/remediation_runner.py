from core.approval import load_requests, mark_executed
from core.remediation import create_remediation, start_remediation, complete_remediation
from core.docker_actions import execute_action, container_status
from core.remediation_memory import record_result
from core.execution_audit import record as record_audit
from core.decision_engine import load_decisions
from core.logger import info


def get_approved():
    # Confirmed live 2026-07-28 (13N): build architecture/deploy approvals
    # (core.approval.create_build_approval, which sets build_id) share this
    # same approval-queue/status field with genuine incident-remediation
    # approvals (core.approval.create_request, which never sets build_id).
    # Without this filter, an approved build request -- e.g.
    # service="kai-build:5353f7e0", action="approve_deploy" -- gets swept
    # into process() below and run through container_status()/
    # create_remediation() as if it were a real Docker action against a
    # container named "kai-build:5353f7e0", corrupting remediation history
    # for every build/deploy approval.
    return [
        r for r in load_requests()
        if r.get("status") == "approved" and not r.get("build_id")
    ]


def find_root_cause(incident_id, action, fallback):

    for decision in load_decisions():

        if decision.get("incident_id") == incident_id and decision.get("recommended_action") == action:

            return decision.get("reason", fallback)

    return fallback


def _safe_record_result(request, result):
    try:
        record_result(
            request.get("incident"),
            request["action"],
            result.get("status", "failed"),
            issue=request.get("reason"),
            root_cause=find_root_cause(request.get("incident"), request["action"], request.get("reason"))
        )
    except Exception as error:
        info(f"remediation result record failed: {type(error).__name__}: {error}")


def _safe_record_audit(request, remediation, result):
    try:
        record_audit({
            "operator": request.get("approved_by") or "unknown",
            "action": request["action"],
            "service": request["service"],
            "command": f"{request['action']} on {request['service']}",
            "result": result.get("status", "failed"),
            "request_id": request["id"],
            "remediation_id": remediation["id"] if remediation else None
        })
    except Exception as error:
        info(f"execution audit record failed: {type(error).__name__}: {error}")


def _process_request(request):
    """Execute one approved remediation request.

    Never raises: before the Docker guard, a missing `docker` binary made
    container_status() raise FileNotFoundError here, which propagated out of
    run_cycle() and aborted the whole orchestrator cycle every tick. Each
    side effect is individually guarded, and the request is always marked
    terminal (`executed`) so a failing action can never re-trigger forever.
    """

    remediation = None
    result = {"status": "failed"}

    try:
        before = container_status(request["service"])

        remediation = create_remediation(
            approval_id=request["id"],
            trace_id=request.get("incident"),
            action=request["action"],
            service=request["service"]
        )

        start_remediation(remediation["id"], snapshot={
            "before": before,
            "command": f"{request['action']} on {request['service']}",
            "expected_result": "container running"
        })

        try:
            result = execute_action(request["action"], request["service"])
        except Exception as error:
            result = {"status": "failed", "error": f"{type(error).__name__}: {error}"}

        complete_remediation(remediation["id"], result)

    except Exception as error:
        result = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        info(f"remediation request {request.get('id')} failed: {type(error).__name__}: {error}")

    _safe_record_result(request, result)
    _safe_record_audit(request, remediation, result)

    try:
        mark_executed(request["id"])
    except Exception as error:
        info(f"remediation request {request.get('id')} could not be marked executed: {type(error).__name__}: {error}")

    return {
        "request_id": request["id"],
        "remediation_id": remediation["id"] if remediation else None,
        "service": request["service"],
        "trace_id": request.get("incident"),
        "status": result.get("status", "failed"),
        "result": result
    }


def process():

    results = []

    for request in get_approved():

        results.append(_process_request(request))

    return results


if __name__ == "__main__":
    print(process())
