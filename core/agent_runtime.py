from core.agents.orchestrator_agent import AgentOrchestrator
from core.agents.supervisor_agent import SupervisorAgent
from core.autonomous_executor import execute_autonomous_actions


def run_agent_cycle():

    orchestrator = AgentOrchestrator()
    supervisor = SupervisorAgent()


    context = {
        "services": [
            {
                "name": "pulse",
                "status": "unhealthy"
            }
        ]
    }


    pipeline = orchestrator.run(
        context
    )


    supervision = supervisor.analyze(
        {
            "plans": pipeline.get("plans", [])
        }
    )


    execution = execute_autonomous_actions()


    return {
        "pipeline": pipeline,
        "supervision": supervision,
        "execution": execution
    }



if __name__ == "__main__":

    print(
        run_agent_cycle()
    )
