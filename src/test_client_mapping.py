import unittest
from vault_client import vault_client

class TestClientMapping(unittest.TestCase):
    def test_mapping(self):
        with open('vault_client.py', 'r') as file:
            lines = file.readlines()
        
        mapping_line = lines[111]  # Adjusted for 0-based index
        mapping = eval(mapping_line.strip())
        
        self.assertIn('kai-vault', mapping)
        self.assertIn('vaultwarden', mapping)
        
        self.assertEqual(mapping['kai-vault'], '127.0.0.1:8120')
        self.assertEqual(mapping['vaultwarden'], 'CT107:8222')

if __name__ == '__main__':
    unittest.main()
