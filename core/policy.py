from core.memory import load, save
from datetime import datetime


POLICIES = {
    "auto_restart_allowed": False,
    "critical_requires_approval": True,
    "maintenance_window": "02:00-04:00",
}


def load_policy():

    stored = load("policy.json")

    if not stored:
        save(
            "policy.json",
            POLICIES
        )
        return POLICIES

    return stored



def check_action(
    action,
    severity="info"
):

    policy = load_policy()

    decision = {
        "action": action,
        "severity": severity,
        "approved": False,
        "reason": "",
        "checked": datetime.now().isoformat()
    }


    if severity == "critical":

        if policy.get(
            "critical_requires_approval",
            True
        ):

            decision["reason"] = (
                "Critical actions require human approval"
            )

            return decision


    if action == "restart_service":

        if policy.get(
            "auto_restart_allowed",
            False
        ):

            decision["approved"] = True
            decision["reason"] = (
                "Automatic restart allowed by policy"
            )

        else:

            decision["reason"] = (
                "Automatic restart disabled"
            )


    return decision



if __name__ == "__main__":

    print(
        check_action(
            "restart_service",
            "warning"
        )
    )
