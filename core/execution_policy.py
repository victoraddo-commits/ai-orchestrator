ALLOWED_ACTIONS = [
    "restart_container",
    "monitor",
]


def allowed(action):
    return action in ALLOWED_ACTIONS
