from core import config


LOW_RISK_ACTIONS = {
    "restart_monitoring_agent",
    "clear_temp_files",
    "restart_container",
}

MEDIUM_RISK_ACTIONS = {
    "resize_resources",
    "restart_production_service",
}

HIGH_RISK_ACTIONS = {
    "migrate_vm",
    "shutdown_node",
    "modify_networking",
    "delete_resources",
}


def risk_tier(action):

    if action in LOW_RISK_ACTIONS:
        return "low"

    if action in MEDIUM_RISK_ACTIONS:
        return "medium"

    if action in HIGH_RISK_ACTIONS:
        return "high"

    return "unknown"


def requires_approval(action):

    tier = risk_tier(action)

    if tier == "low" and config.AUTONOMOUS_MODE:
        return False

    return True
