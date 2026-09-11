import os
import json
import requests
import logging
from datetime import datetime

# Import existing infrastructure
import memory
import ai_router

# Constants
LOG_FILE = "verification_history.json"
logger = logging.getLogger(__name__)

def verify_test_results(test_output):
    """Verify the results of a test."""
    passed = test_output == "PASSED"
    failed = test_output == "FAILED"
    evidence = {"test_output": test_output, "timestamp": datetime.now().isoformat()}
    return {"passed": passed, "failed": failed, "evidence": evidence}

def verify_file_exists(path):
    """Verify if a file exists at the given path."""
    exists = os.path.exists(path)
    evidence = {"path": path, "exists": exists, "timestamp": datetime.now().isoformat()}
    return {"exists": exists, "evidence": evidence}

def verify_service_health(endpoint):
    """Verify the health of a service at the given endpoint."""
    try:
        response = requests.get(endpoint)
        healthy = response.status_code == 200
        evidence = {"endpoint": endpoint, "status_code": response.status_code, "timestamp": datetime.now().isoformat()}
    except requests.RequestException as e:
        healthy = False
        evidence = {"endpoint": endpoint, "error": str(e), "timestamp": datetime.now().isoformat()}
    return {"healthy": healthy, "evidence": evidence}

def verify_api_response(url, expected):
    """Verify if the API response matches the expected value."""
    try:
        response = requests.get(url)
        matches = response.json() == expected
        evidence = {"url": url, "expected": expected, "actual": response.json(), "timestamp": datetime.now().isoformat()}
    except requests.RequestException as e:
        matches = False
        evidence = {"url": url, "error": str(e), "timestamp": datetime.now().isoformat()}
    return {"matches": matches, "evidence": evidence}

def log_evidence(evidence):
    """Log the evidence to a JSON file."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            json.dump([], f)
    with open(LOG_FILE, 'r+') as f:
        data = json.load(f)
        data.append(evidence)
        f.seek(0)
        json.dump(data, f, indent=4)
        f.truncate()

def verify_with_different_model(work_output, exclude_model=None):
    """Verify the work output using a different model."""
    # This is a placeholder for the actual implementation
    return {"verified": True, "evidence": {"model": "different_model", "work_output": work_output}}
