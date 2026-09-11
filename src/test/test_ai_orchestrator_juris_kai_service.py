import subprocess
import time

def test_service_status():
    result = subprocess.run(['systemctl', 'is-active', 'ai-orchestrator-juris-kai.service'], capture_output=True, text=True)
    assert result.stdout.strip() == 'active', f"Service is not active: {result.stdout}"

def test_service_logs():
    subprocess.run(['journalctl', '-u', 'ai-orchestrator-juris-kai.service', '-f'], check=True)

def test_bot_response():
    # Assuming there is a bot interface that can be interacted with via a command
    response = subprocess.run(['curl', 'http://localhost:8080/ping'], capture_output=True, text=True)
    assert 'pong' in response.stdout, f"Bot did not respond correctly: {response.stdout}"

def test_no_409_error():
    # Assuming there is a way to trigger a command that might cause a 409 error
    response = subprocess.run(['curl', 'http://localhost:8080/some_command'], capture_output=True, text=True)
    assert '409' not in response.stdout, f"409 error occurred: {response.stdout}"
