import requests
from requests.exceptions import RequestException

class ServiceUnavailableError(Exception):
    pass

def check_kai_vault_health():
    try:
        response = requests.get('http://127.0.0.1:8120/v1/sys/health', timeout=2)
        if response.status_code in [200, 429]:
            return True
    except RequestException:
        pass
    return False

def check_vaultwarden_health():
    try:
        response = requests.get('http://CT107:8222/api/status', timeout=2)
        if response.status_code in [200, 429]:
            return True
    except RequestException:
        pass
    return False

def discover_services():
    kai_vault_active = check_kai_vault_health()
    vaultwarden_active = check_vaultwarden_health()
    
    if not kai_vault_active and not vaultwarden_active:
        raise ServiceUnavailableError("Both kai-vault and vaultwarden are unavailable.")
    
    return {
        'kai-vault': '127.0.0.1:8120' if kai_vault_active else None,
        'vaultwarden': 'CT107:8222' if vaultwarden_active else None
    }
