def restart_container(service):

    print(f"Restarting container: {service}")

    return {
        "status": "success",
        "action": "restart_container",
        "service": service
    }
