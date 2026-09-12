import unittest
from vault_client import VaultClient
from threading import Thread
import time

class TestAuthSecurity(unittest.TestCase):
    def test_session_revocation(self):
        vault_client = VaultClient()
        token = vault_client.authenticate('Creds_A')
        
        # Change password
        vault_client.change_password('Creds_A')
        
        # Attempt to read secret with old token
        with self.assertRaises(Exception) as context:
            vault_client.read_secret(token, 'secret/data/ai-orchestrator-test')
        
        self.assertIn('401 Unauthorized', str(context.exception))

    def test_non_parallel_vault_enforcement(self):
        def access_vault():
            vault_client = VaultClient()
            vault_client.read_secret('token', 'secret/data/ai-orchestrator-test')
        
        thread1 = Thread(target=access_vault)
        thread2 = Thread(target=access_vault)
        
        thread1.start()
        time.sleep(1)  # Ensure thread1 has started
        thread2.start()
        
        thread1.join()
        thread2.join()
        
        self.assertTrue(thread2.is_alive())  # Should block or throw an exception

if __name__ == '__main__':
    unittest.main()
