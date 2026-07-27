import sys

from core.approval import load_requests, approve, reject
from core.lifecycle import InvalidTransition


def list_requests():

    requests = load_requests()

    if not requests:
        print("No pending approvals")
        return


    print("\nAPPROVAL QUEUE\n")

    print(
        f"{'ID':10} {'ACTION':20} {'SERVICE':20} {'STATUS'}"
    )

    print("-" * 70)


    for req in requests:

        print(
            f"{req['id']:10} {req['action']:20} {req['service']:20} {req['status']}"
        )



def run_transition(request_id, action, label):

    try:

        result = action(request_id)

    except InvalidTransition as error:

        print(f"Cannot {label} request {request_id}: {error}")
        return


    if result is None:

        print("Request not found")
        return


    print(f"Request {request_id} marked {result['status']}")



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

        run_transition(sys.argv[2], approve, "approve")


    elif command == "reject":

        run_transition(sys.argv[2], reject, "reject")


    else:

        print(
            "Unknown command"
        )
