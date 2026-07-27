from core.actions.proxmox_actions import restart_lxc


def restart_container(service):

    print(f"Restarting container: {service}")

    return restart_lxc(service)
