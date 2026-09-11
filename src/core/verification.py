import os
import json
import requests
import logging
from datetime import datetime
import memory
import ai_router

# Constants & Setup
VERIFICATION_LOG = "verification_history.json"
logger = logging.getLogger(__name__)

# --- Helper: Persistence ---
def _log_verification(target, method, result, evidence, verifier_model):
    """Appends a record to verification_history.json using memory.py"""
    record = {
        "verification_id": datetime.utcnow().isoformat(),
        "target": target,
        "method": method,
        "result": result,
        "evidence": evidence,
        "timestamp": datetime.utcnow().isoformat(),
        "verifier_model": verifier_model
    }
    # Implementation details to read, append, and save using memory module
    existing_records = memory.load(VERIFICATION_LOG, default=[])
    existing_records.append(record)
    memory.save(VERIFICATION_LOG, existing_records)

# --- Function: Evidence-based Checks ---
def verify_test_results(test_output):
    """Verifies test logs and returns pass/fail status."""
    passed = "PASSED" in test_output
    failed = "FAILED" in test_output
    evidence = f"Test output: {test_output[:100]}..."
    _log_verification("test_results", "verify_test_results", passed, evidence, "local_model")
    return {"passed": passed, "failed": failed, "evidence": evidence}

def verify_file_exists(path):
    """Checks file system existence."""
    exists = os.path.exists(path)
    evidence = f"File path: {path}, exists: {exists}"
    _log_verification("file_exists", "verify_file_exists", exists, evidence, "local_model")
    return {"exists": exists, "evidence": evidence}

def verify_service_health(endpoint):
    """Checks HTTP 200 OK status."""
    try:
        response = requests.head(endpoint, timeout=5)
        healthy = 200 <= response.status_code < 400
        evidence = f"Endpoint: {endpoint}, status code: {response.status_code}, latency: {response.elapsed.total_seconds() * 1000:.2f} ms"
        _log_verification("service_health", "verify_service_health", healthy, evidence, "local_model")
        return {"healthy": healthy, "evidence": evidence}
    except requests.RequestException as e:
        evidence = f"Endpoint: {endpoint}, error: {str(e)}"
        _log_verification("service_health", "verify_service_health", False, evidence, "local_model")
        return {"healthy": False, "evidence": evidence}

def verify_api_response(url, expected):
    """Checks API JSON response matches expected payload."""
    try:
        response = requests.get(url)
        response_json = response.json()
        matches = response_json == expected
        evidence = f"URL: {url}, actual response: {response_json}, expected: {expected}, matches: {matches}"
        _log_verification("api_response", "verify_api_response", matches, evidence, "local_model")
        return {"matches": matches, "evidence": evidence}
    except (requests.RequestException, json.JSONDecodeError) as e:
        evidence = f"URL: {url}, error: {str(e)}"
        _log_verification("api_response", "verify_api_response", False, evidence, "local_model")
        return {"matches": False, "evidence": evidence}

# --- Function: Independent Review ---
def verify_with_different_model(work_output, original_model):
    """Uses ai_router to verify work from a different model."""
    prompt = f"Please verify the following work output: {work_output}"
    try:
        result = ai_router.delegate(prompt, exclude_model=original_model)
        verified = result.strip().lower() == "verified"
        evidence = f"Verification result: {result}"
        _log_verification("independent_review", "verify_with_different_model", verified, evidence, original_model)
        return {"verified": verified, "evidence": evidence}
    except Exception as e:
        evidence = f"Error during verification: {str(e)}"
        _log_verification("independent_review", "verify_with_different_model", False, evidence, original_model)
        return {"verified": False, "evidence": evidence}

# --- Function: Mission Integration ---
def verify_task(task):
    """Executes task.verify if callable, returns result."""
    if hasattr(task, 'verify') and callable(task.verify):
        try:
            result = task.verify()
            _log_verification("mission_task", "verify_task", result, str(result), "local_model")
            return result
        except Exception as e:
            evidence = f"Error during task verification: {str(e)}"
            _log_verification("mission_task", "verify_task", False, evidence, "local_model")
            return None
    else:
        _log_verification("mission_task", "verify_task", False, "Task does not have a callable verify method", "local_model")
        return None

# --- Execution Hook ---
if __name__ == "__main__":
    # Basic sanity check or manual run capability
    test_output = "Test output: PASSED"
    print(verify_test_results(test_output))
    print(verify_file_exists("path/to/file.txt"))
    print(verify_service_health("https://api.example.com"))
    print(verify_api_response("https://api.example.com/data", {"key": "value"}))
    print(verify_with_different_model("Work output to verify", "original_model"))
    task = type('Task', (), {'verify': lambda self: True})
    print(verify_task(task()))
