import requests


def status():

    return {
        "message":
        "Proxmox API connector pending"
    }


if __name__ == "__main__":
    print(status())
