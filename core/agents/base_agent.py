from datetime import datetime


class BaseAgent:

    name = "base"

    def __init__(self):

        self.created = datetime.now().isoformat()


    def analyze(self, context):

        raise NotImplementedError(
            "Agent must implement analyze()"
        )


    def execute(self, context):

        raise NotImplementedError(
            "Agent must implement execute()"
        )


    def report(self, result):

        return {
            "agent": self.name,
            "timestamp": datetime.now().isoformat(),
            "result": result
        }
