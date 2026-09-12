import unittest
from vault_client import VaultClient

class TestOperations(unittest.TestCase):
    def test_key_rotation(self):
        vault_client = VaultClient()
        response = vault_client.rotate_key('secret/data/ai-orchestrator-test')
        
        self.assertTrue(response['success'])

    def test_audit_trail(self):
        vault_client = VaultClient()
        response = vault_client.rotate_key('secret/data/ai-orchestrator-test')
        request_id = response['request_id']
        
        audit_entries = vault_client.get_audit_entries()
        audit_entry = next((entry for entry in audit_entries if entry['request_id'] == request_id), None)
        
        self.assertIsNotNone(audit_entry)
        self.assertIn('rotation', audit_entry['action'])

if __name__ == '__main__':
    unittest.main()
