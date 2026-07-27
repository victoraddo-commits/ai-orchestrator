from datetime import datetime

from core.execution_queue import get_queue
from core.memory import save
from core.autonomous_executor import execute_autonomous_actions
from core.remediation_memory import record_result


QUEUE_FILE = "execution_queue.json"


def update_queue(queue):

    save(
        QUEUE_FILE,
        queue
    )


def run():

    queue = get_queue()

    pending = [
        item for item in queue
        if item.get("status") == "pending"
    ]

    if not pending:
        print("No pending executions")
        return []


    results = []


    for item in pending:

        item["status"] = "running"
        item["started"] = datetime.now().isoformat()

        update_queue(queue)


        try:

            execution = execute_autonomous_actions()


            item["status"] = "completed"
            item["completed"] = datetime.now().isoformat()


            result = "success"


        except Exception as error:

            item["status"] = "failed"
            item["error"] = str(error)

            result = "failure"



        record_result(
            item.get("incident"),
            item.get("action"),
            result
        )


        results.append(
            {
                "incident": item.get("incident"),
                "action": item.get("action"),
                "status": item.get("status")
            }
        )


        update_queue(queue)


    return results



if __name__ == "__main__":

    print(run())
