import os
import tempfile
import subprocess
from dotenv import load_dotenv

class ScopedOpenCodeRunner:
    @staticmethod
    def run_opencode_with_auth(key, model_name, command_args):
        # Load the environment variables
        load_dotenv()

        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a temporary auth.json file
            auth_json_path = os.path.join(temp_dir, 'auth.json')
            with open(auth_json_path, 'w') as auth_file:
                auth_file.write(f'{{"opencode":{{"api_key":"{key}","model":"{model_name}"}}}}')

            # Set the environment variable to point to the temporary auth.json file
            os.environ['OPCODE_AUTH_PATH'] = auth_json_path

            # Run the OpenCode CLI command
            subprocess.run(['opencode-cli'] + command_args)

            # Clean up the temporary directory
            os.rmdir(temp_dir)
