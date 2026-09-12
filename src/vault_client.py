import requests
from threading import Lock

class VaultClient:
    _instance = None
    _instance_lock = Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(VaultClient, cls).__new__(cls)
                cls._instance.session = requests.Session()
                cls._instance.lock = Lock()
        return cls._instance

    def authenticate(self, credentials):
        response = self.session.post('http://127.0.0.1:8120/v1/auth/userpass/login', json={'username': credentials['username'], 'password': credentials['password']})
        response.raise_for_status()
        return response.json()['auth']['client_token']

    def change_password(self, credentials):
        response = self.session.post('http://127.0.0.1:8120/v1/auth/userpass/users/{}?force=true'.format(credentials['username']), json={'password': credentials['new_password']})
        response.raise_for_status()

    def read_secret(self, token, path):
        with self.lock:
            response = self.session.get('http://127.0.0.1:8120/v1/{}?token={}'.format(path, token))
            response.raise_for_status()
            return response.json()['data']

    def rotate_key(self, path):
        with self.lock:
            response = self.session.post('http://127.0.0.1:8120/v1/sys/renew', json={'path': path})
            response.raise_for_status()
            return response.json()

    def get_audit_entries(self):
        with self.lock:
            response = self.session.get('http://127.0.0.1:8120/v1/sys/audit/')
            response.raise_for_status()
            return response.json()['data']

# Example usage
if __name__ == '__main__':
    vault_client = VaultClient()
    token = vault_client.authenticate({'username': 'user', 'password': 'pass'})
    secret = vault_client.read_secret(token, 'secret/data/ai-orchestrator-test')
    print(secret)
