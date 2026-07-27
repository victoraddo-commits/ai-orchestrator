from core.agents.base_agent import BaseAgent


class AnalystAgent(BaseAgent):

    name = "analyst"


    def analyze(self, context):

        findings = context.get(
            "findings",
            []
        )

        analysis = []


        for finding in findings:

            incident = finding.get(
                "incident"
            )

            issue = finding.get(
                "issue"
            )


            severity = "warning"
            confidence = 50


            if issue in [
                "critical",
                "failed",
                "unhealthy"
            ]:
                severity = "critical"
                confidence = 85


            analysis.append(
                {
                    "incident": incident,
                    "service": finding.get("service"),
                    "severity": severity,
                    "confidence": confidence,
                    "assessment": "service_health_issue"
                }
            )


        return analysis



if __name__ == "__main__":

    agent = AnalystAgent()

    print(
        agent.analyze(
            {
                "findings": [
                    {
                        "service": "pulse",
                        "issue": "unhealthy"
                    }
                ]
            }
        )
    )
