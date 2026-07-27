from core.agents.observer_agent import ObserverAgent
from core.agents.analyst_agent import AnalystAgent
from core.agents.planner_agent import PlannerAgent


class AgentOrchestrator:


    def __init__(self):

        self.observer = ObserverAgent()
        self.analyst = AnalystAgent()
        self.planner = PlannerAgent()



    def run(self, context):


        findings = self.observer.analyze(
            context
        )


        assessments = self.analyst.analyze(
            {
                "findings": findings
            }
        )


        plans = self.planner.analyze(
            {
                "assessments": assessments
            }
        )


        return {
            "findings": findings,
            "assessments": assessments,
            "plans": plans
        }



if __name__ == "__main__":


    orchestrator = AgentOrchestrator()


    result = orchestrator.run(
        {
            "services": [
                {
                    "name": "pulse",
                    "status": "unhealthy"
                }
            ]
        }
    )


    print(result)
