from core.agents.base_agent import BaseAgent
from core.learning_decision import adjust_confidence
from core.risk_engine import evaluate_risk
from core.execution_queue import enqueue


class SupervisorAgent(BaseAgent):

    name = "supervisor"


    def analyze(self, context):

        plans = context.get(
            "plans",
            []
        )

        results = []


        for plan in plans:

            recommendation = {
                "recommendation": plan.get("action"),
                "confidence": 85
            }


            adjusted = adjust_confidence(
                recommendation
            )


            risk = evaluate_risk(
                {
                    "severity": "critical",
                    "occurrences": 3
                },
                adjusted
            )


            decision = {
                "incident": plan.get("service"),
                "action": plan.get("action"),
                "confidence": adjusted.get("confidence"),
                "risk": risk
            }


            if risk.get("auto_execute"):
                enqueue(decision)


            results.append(
                decision
            )


        return results



if __name__ == "__main__":

    supervisor = SupervisorAgent()


    print(
        supervisor.analyze(
            {
                "plans": [
                    {
                        "service": "pulse",
                        "action": "restart_container"
                    }
                ]
            }
        )
    )
