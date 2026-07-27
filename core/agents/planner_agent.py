from core.agents.base_agent import BaseAgent


class PlannerAgent(BaseAgent):

    name = "planner"


    def analyze(self, context):

        assessments = context.get(
            "assessments",
            []
        )

        plans = []


        for assessment in assessments:

            severity = assessment.get(
                "severity"
            )

            service = assessment.get(
                "service"
            )


            action = "monitor"
            priority = "low"
            approval = True


            if severity == "critical":

                action = "restart_container"
                priority = "high"
                approval = False


            plans.append(
                {
                    "service": service,
                    "action": action,
                    "priority": priority,
                    "approval_required": approval,
                    "rollback": "restore_previous_state"
                }
            )


        return plans



if __name__ == "__main__":

    agent = PlannerAgent()

    print(
        agent.analyze(
            {
                "assessments": [
                    {
                        "service": "pulse",
                        "severity": "critical",
                        "confidence": 85
                    }
                ]
            }
        )
    )
