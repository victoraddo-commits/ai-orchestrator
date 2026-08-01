from core.approval import load_requests, mark_executed
from core.remediation import create_remediation, start_remediation, complete_remediation
from core.docker_actions import execute_action, container_status
from core.remediation_memory import record_result
from core.execution_audit import record as record_audit
from core.decision_engine import load_decisions


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


def process():

    results = []

    for request in get_approved():

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
            result = {"status": "failed", "error": str(error)}

        complete_remediation(remediation["id"], result)

        mark_executed(request["id"])

        record_result(
            request.get("incident"),
            request["action"],
            result.get("status", "failed"),
            issue=request.get("reason"),
            root_cause=find_root_cause(request.get("incident"), request["action"], request.get("reason"))
        )

        record_audit({
            "operator": request.get("approved_by") or "unknown",
            "action": request["action"],
            "service": request["service"],
            "command": f"{request['action']} on {request['service']}",
            "result": result.get("status", "failed"),
            "request_id": request["id"],
            "remediation_id": remediation["id"]
        })

        results.append({
            "request_id": request["id"],
            "remediation_id": remediation["id"],
            "service": request["service"],
            "trace_id": request.get("incident"),
            "status": result.get("status", "failed"),
            "result": result
        })

    return results


if __name__ == "__main__":
    print(process())
