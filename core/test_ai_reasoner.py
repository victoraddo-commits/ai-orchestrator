from core.ai_reasoner import analyze_incident
from core.memory import load


def run():

    incidents = load(
        "incidents.json"
    ) or []


    for incident in incidents:

        result = analyze_incident(
            incident
        )

        print(result)


if __name__ == "__main__":
    run()
