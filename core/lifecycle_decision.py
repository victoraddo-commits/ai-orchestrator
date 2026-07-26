from core.decision_engine import evaluate_incidents
from core.lifecycle_controller import (
    move_to_investigating
)


def evaluate_with_lifecycle():

    decisions = evaluate_incidents()

    results = []


    for decision in decisions:

        incident_id = decision.get(
            "incident"
        )

        lifecycle = move_to_investigating(
            str(incident_id)
        )


        results.append({

            "incident": incident_id,

            "decision": decision,

            "lifecycle": lifecycle

        })


    return results



if __name__ == "__main__":

    print(
        evaluate_with_lifecycle()
    )
