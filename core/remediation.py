from core.memory import load, save
from core.actions import execute
from core.action_log import record
from datetime import datetime


def get_approved_requests():

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        return []


    return [
        r for r in requests
        if r.get("status") == "approved"
    ]



def verify_request(request):

    required = (
        "action",
        "service",
        "reason"
    )


    for field in required:

        if not request.get(field):

            return False


    return True



def process_approved():

    requests = get_approved_requests()

    results = []


    for request in requests:

        if not verify_request(request):

            results.append(
                record(
                    request.get("action"),
                    request.get("service"),
                    "validation-failed"
                )
            )

            continue


        result = {

            "timestamp": datetime.now().isoformat(),

            "request_id": request["id"],

            "action": request["action"],

            "service": request["service"],

            "status": "validated",

            "execution": "dry-run"

        }


        results.append(result)


    return results



if __name__ == "__main__":

    print(
        process_approved()
    )
