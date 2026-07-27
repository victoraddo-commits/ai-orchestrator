from core.agents.base_agent import BaseAgent


class ObserverAgent(BaseAgent):

    name = "observer"


    def analyze(self, context):

        findings = []

        services = context.get(
            "services",
            []
        )


        for service in services:

            if service.get("status") != "healthy":

                findings.append(
                    {
                        "service": service.get("name"),
                        "issue": service.get("status")
                    }
                )


        return findings



if __name__ == "__main__":

    agent = ObserverAgent()

    print(
        agent.analyze(
            {
                "services": [
                    {
                        "name": "pulse",
                        "status": "unhealthy"
                    }
                ]
            }
        )
    )
