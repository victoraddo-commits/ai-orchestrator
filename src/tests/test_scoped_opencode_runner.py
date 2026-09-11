import unittest
from core.ai.providers.scoped_opencode_runner import ScopedOpenCodeRunner

class TestScopedOpenCodeRunner(unittest.TestCase):
    def test_run_opencode_with_auth(self):
        key = "test_key"
        model_name = "test_model"
        command_args = ["test_command", "arg1", "arg2"]

        # Mock the subprocess.run function
        subprocess.run = unittest.mock.Mock()

        # Call the function
        ScopedOpenCodeRunner.run_opencode_with_auth(key, model_name, command_args)

        # Check if the subprocess.run function was called with the correct arguments
        subprocess.run.assert_called_once_with(['opencode-cli', 'test_command', 'arg1', 'arg2'], env={'OPCODE_AUTH_PATH': 'test_dir/auth.json'})

if __name__ == '__main__':
    unittest.main()
