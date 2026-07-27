from core.remediation_memory import get_history
from core.decision_memory import get_decisions


class AgentContextManager:


    def build_context(self, service):

        remediation_history = get_history()

        decisions = get_decisions()


        service_history = []


        for item in remediation_history:

            if item.get("incident") == service:
                service_history.append(item)


        service_decisions = []


        for decision in decisions:

            if decision.get("incident") == service:
                service_decisions.append(decision)


        return {
            "service": service,
            "remediation_history": service_history,
            "decision_history": service_decisions
        }



if __name__ == "__main__":

    manager = AgentContextManager()

    print(
        manager.build_context(
            "5"
        )
    )
