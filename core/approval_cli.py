import sys

from core.memory import load, save


def list_requests():

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        print("No pending approvals")
        return


    print("\nPENDING APPROVALS\n")

    print(
        f"{'ID':10} {'ACTION':20} {'SERVICE':20} {'STATUS'}"
    )

    print("-" * 70)


    for req in requests:

        print(
            f"{req['id']:10} {req['action']:20} {req['service']:20} {req['status']}"
        )



def update_request(request_id, status):

    requests = load(
        "approval_queue.json"
    )

    updated = False


    for req in requests:

        if req["id"] == request_id:

            req["status"] = status
            updated = True


    if updated:

        save(
            "approval_queue.json",
            requests
        )

        print(
            f"Request {request_id} marked {status}"
        )

    else:

        print(
            "Request not found"
        )



if __name__ == "__main__":

    if len(sys.argv) < 2:

        print(
            "Usage: list | approve <id> | reject <id>"
        )

        sys.exit(1)


    command = sys.argv[1]


    if command == "list":

        list_requests()


    elif command == "approve":

        update_request(
            sys.argv[2],
            "approved"
        )


    elif command == "reject":

        update_request(
            sys.argv[2],
            "rejected"
        )


    else:

        print(
            "Unknown command"
        )
