import os
import sys
import getpass

from core.approval import load_requests, approve, reject
from core.lifecycle import InvalidTransition


def get_operator_identity():
    return os.environ.get("SUDO_USER") or getpass.getuser()


def confirm(prompt):
    response = input(f"{prompt} [type 'yes' to confirm]: ")
    return response.strip().lower() == "yes"


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



def run_transition(request_id, action_fn, label, skip_confirm=False, confirm_fn=confirm):

    if not skip_confirm and not confirm_fn(f"About to {label} request {request_id}"):
        print("Cancelled.")
        return None


    operator = get_operator_identity()

    try:

        result = action_fn(request_id, operator=operator)

    except InvalidTransition as error:

        print(f"Cannot {label} request {request_id}: {error}")
        return None


    if result is None:

        print("Request not found")
        return None


    print(f"Request {request_id} marked {result['status']} by {operator}")

    return result



if __name__ == "__main__":

    if len(sys.argv) < 2:

        print(
            "Usage: list | approve <id> [--yes] | reject <id> [--yes]"
        )

        sys.exit(1)


    command = sys.argv[1]
    skip_confirm = "--yes" in sys.argv


    if command == "list":

        list_requests()


    elif command == "approve":

        run_transition(sys.argv[2], approve, "approve", skip_confirm=skip_confirm)


    elif command == "reject":

        run_transition(sys.argv[2], reject, "reject", skip_confirm=skip_confirm)


    else:

        print(
            "Unknown command"
        )
