"""Multi-stage coding validation and inference testing.

Every candidate model MUST pass real API inference tests before becoming available.
Tests include: basic coding, debugging, repository reasoning, TDD, tool calling,
long-context, self-correction, and architecture design.
"""

import json
import time
import subprocess
import requests
from datetime import datetime
from typing import Optional

from . import OMNIROUTE_BASE_URL

# OpenRouter API base
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

# Get API key fresh at runtime to ensure it picks up .env values
from . import OPENROUTER_API_KEY as _or_key
OPENROUTER_API_KEY = _or_key

from .models import db


class ValidationError(Exception):
    """Error during model validation."""
    pass


def get_inference_headers() -> dict:
    """Get headers for OpenRouter API inference calls."""
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }


def run_inference(model_id: str, prompt: str, timeout: int = 60) -> tuple[bool, str, float]:
    """Run a single inference request against OpenRouter API using subprocess curl.

    Uses subprocess to avoid Python requests hanging on this server.

    Returns: (success, response_text, latency_ms)
    """
    start_time = time.time()

    try:
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
        })

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"{OPENROUTER_API_BASE}/chat/completions",
                "-H", f"Authorization: Bearer {OPENROUTER_API_KEY}",
                "-H", "Content-Type: application/json",
                "-d", payload,
            ],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        latency_ms = (time.time() - start_time) * 1000

        if not result.stdout:
            db.update_status(model_id, "FAILING", "Empty response from API")
            return False, "", latency_ms

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            db.update_status(model_id, "FAILING", f"Invalid JSON: {result.stdout[:200]}")
            return False, "", latency_ms

        # Check for API errors
        if "error" in data:
            error_msg = data.get("error", {}).get("message", str(data.get("error", "")))
            if "429" in error_msg or data.get("error", {}).get("code") == 429:
                db.update_status(model_id, "RATE_LIMITED", error_msg)
            elif data.get("error", {}).get("code") in (500, 502, 503, 504):
                db.update_status(model_id, "FAILING", error_msg)
            else:
                db.update_status(model_id, "FAILING", error_msg[:200])
            return False, "", latency_ms

        choices = data.get("choices", [])
        if not choices:
            db.update_status(model_id, "FAILING", "No choices in response")
            return False, "", latency_ms

        message = choices[0].get("message", {})
        # Handle reasoning models: extract the actual answer
        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""

        # Reasoning models put the answer in reasoning; use that as the response
        if reasoning and not content:
            final_text = reasoning.strip()
        elif content:
            final_text = content.strip()
        else:
            final_text = reasoning.strip() if reasoning.strip() else ""

        if not final_text:
            db.update_status(model_id, "FAILING", "Empty response")
            return False, "", latency_ms

        return True, final_text, latency_ms

    except subprocess.TimeoutExpired:
        latency_ms = (time.time() - start_time) * 1000
        db.update_status(model_id, "FAILING", "Timeout")
        return False, "", latency_ms
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        db.update_status(model_id, "FAILING", str(e))
        return False, "", latency_ms


def run_omniroute_inference(model_id: str, prompt: str, timeout: int = 60) -> tuple[bool, str, float]:
    """Run inference through OmniRoute gateway."""
    start_time = time.time()

    try:
        # First try direct OpenRouter
        success, content, latency_ms = run_inference(model_id, prompt, timeout)
        if success:
            return success, content, latency_ms

        # Fallback: try through OmniRoute if the model is available there
        response = requests.post(
            f"{OMNIROUTE_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
            },
            timeout=timeout
        )

        latency_ms = (time.time() - start_time) * 1000

        if not response.ok:
            return False, "", latency_ms

        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        return bool(content), content, latency_ms

    except Exception:
        latency_ms = (time.time() - start_time) * 1000
        return False, "", latency_ms


# Test prompts for multi-stage validation (designed for reasoning models)
TEST_A_BASIC_CODING = """Write a Python function that returns the nth Fibonacci number.
Respond with only the function code."""

TEST_B_DEBUGGING = """The following Python code has a bug. Identify it and show the fix:

```python
def find_max(numbers):
    max_val = 0
    for num in numbers:
        if num > max_val:
            max_val = num
    return max_val
```

Identify the bug, explain the root cause, and provide the corrected code.
Only return your analysis and corrected code, no extra text."""

TEST_C_REPO_REASONING = """I have a project with these files:
- main.py: contains the entry point
- utils.py: contains helper functions
- config.json: contains configuration

Write a small Python module called `analyzer.py` that:
1. Reads config.json
2. Uses utils.py helper functions
3. Processes some data
4. Can be called from main.py

Only return the analyzer.py code."""

TEST_D_TDD = """Create a Python module that:
1. Validates email addresses (basic format check)
2. Validates phone numbers (10+ digits)
3. Has unit tests using pytest

Return the module code followed by tests.
Format as:
---module---
[your module code]
---tests---
[your test code]"""

TEST_E_TOOL_CALLING = """List the files in the current directory using a terminal command.
Use a shell command, not Python. Return the command you would use."""

TEST_F_LONG_CONTEXT = """Analyze this code structure and identify dependencies:

File 1: app/__init__.py - initializes Flask app
File 2: app/routes.py - defines API endpoints
File 3: app/models.py - defines database models
File 4: app/utils.py - helper functions

For each file, list what it imports from the others.
Return as a simple dependency map."""

TEST_G_SELF_CORRECTION = """This implementation is incorrect:

```python
def merge_sorted(list1, list2):
    result = []
    while list1 and list2:
        if list1[0] < list2[0]:
            result.append(list1.pop(0))
        else:
            result.append(list2.pop(0))
    return result + list1 + list2

# Test case that fails:
print(merge_sorted([1, 3, 5], [2, 4, 6]))
```

The output should be [1, 2, 3, 4, 5, 6] but something is wrong.
Fix the implementation and explain what was wrong.
Only return the fixed code and explanation."""

TEST_H_ARCHITECTURE = """Design a production-grade Python class for rate limiting API requests.
Requirements:
- Allow N requests per T seconds
- Thread-safe
- Handle burst traffic gracefully
- Provide methods to check if request is allowed and to reset

Return only the code with docstrings."""


def validate_basic_coding(model_id: str) -> tuple[bool, str]:
    """TEST A — Basic coding validation."""
    success, response, latency = run_inference(model_id, TEST_A_BASIC_CODING, timeout=120)

    if not success:
        return False, f"Basic coding test failed: {response}"

    # Check for valid Python code (reasoning models may embed code in reasoning)
    if "def " not in response:
        return False, "No function found in response"

    # Basic syntax check
    if "return" not in response:
        return False, "No return statement found"

    return True, "Basic coding test passed"


def validate_debugging(model_id: str) -> tuple[bool, str]:
    """TEST B — Debugging validation."""
    success, response, latency = run_inference(model_id, TEST_B_DEBUGGING, timeout=120)

    if not success:
        return False, f"Debugging test failed: {response}"

    # Must identify the bug and provide fix
    response_lower = response.lower()
    has_bug_mention = any(word in response_lower for word in ["bug", "issue", "problem", "wrong", "error", "fix", "corrected"])
    has_code = "def find_max" in response or "max_val" in response

    if not (has_bug_mention and has_code):
        return False, "Did not properly identify and fix the bug"

    return True, "Debugging test passed"


def validate_repo_reasoning(model_id: str) -> tuple[bool, str]:
    """TEST C — Repository reasoning validation."""
    success, response, latency = run_inference(model_id, TEST_C_REPO_REASONING, timeout=120)

    if not success:
        return False, f"Repository reasoning test failed: {response}"

    # Should produce code with imports and function definitions
    response_lower = response.lower()
    has_imports = "import" in response_lower
    has_function = "def " in response or "class " in response

    if not (has_imports and has_function):
        return False, "Did not produce code with proper imports and functions"

    return True, "Repository reasoning test passed"


def validate_tdd(model_id: str) -> tuple[bool, str]:
    """TEST D — Test-driven development validation."""
    success, response, latency = run_inference(model_id, TEST_D_TDD, timeout=60)

    if not success:
        return False, f"TDD test failed: {response}"

    # Should produce both module and tests
    response_lower = response.lower()
    has_validation = any(word in response_lower for word in ["email", "def ", "phone"])
    has_tests = any(word in response_lower for word in ["test", "assert", "pytest", "def test_"])

    if not (has_validation and has_tests):
        return False, "Did not produce both validation code and tests"

    return True, "TDD test passed"


def validate_tool_calling(model_id: str) -> tuple[bool, str]:
    """TEST E — Tool calling / terminal work validation."""
    success, response, latency = run_inference(model_id, TEST_E_TOOL_CALLING, timeout=120)

    if not success:
        return False, f"Tool calling test failed: {response}"

    # Should produce a shell command
    response_lower = response.lower()
    has_command = any(word in response_lower for word in ["ls", "dir", "find", "echo", "$", "./", "|"])

    if not has_command:
        return False, "Did not produce a terminal command"

    return True, "Tool calling test passed"


def validate_long_context(model_id: str) -> tuple[bool, str]:
    """TEST F — Long-context / multi-file reasoning validation."""
    success, response, latency = run_inference(model_id, TEST_F_LONG_CONTEXT, timeout=45)

    if not success:
        return False, f"Long context test failed: {response}"

    # Should analyze dependencies between files
    response_lower = response.lower()
    has_analysis = any(word in response_lower for word in ["import", "depends", "uses", "from", "file"])

    if not has_analysis:
        return False, "Did not produce dependency analysis"

    return True, "Long context test passed"


def validate_self_correction(model_id: str) -> tuple[bool, str]:
    """TEST G — Self-correction validation."""
    success, response, latency = run_inference(model_id, TEST_G_SELF_CORRECTION, timeout=45)

    if not success:
        return False, f"Self-correction test failed: {response}"

    # Should identify bug and provide fix
    response_lower = response.lower()
    has_fix = "def merge_sorted" in response or "merge" in response_lower
    has_explanation = any(word in response_lower for word in ["wrong", "bug", "issue", "problem", "fixed", "correct"])

    if not (has_fix and has_explanation):
        return False, "Did not properly identify and fix the bug"

    return True, "Self-correction test passed"


def validate_architecture(model_id: str) -> tuple[bool, str]:
    """TEST H — Architecture design validation."""
    success, response, latency = run_inference(model_id, TEST_H_ARCHITECTURE, timeout=60)

    if not success:
        return False, f"Architecture test failed: {response}"

    # Should produce a class with proper methods
    response_lower = response.lower()
    has_class = "class " in response and "RateLimiter" in response_lower
    has_methods = response.count("def ") >= 2
    has_docstrings = '"""' in response or "'''" in response

    if not (has_class and has_methods):
        return False, "Did not produce a proper rate limiter class"

    return True, "Architecture test passed"


def run_full_validation(model_id: str, notify_callback=None) -> dict:
    """Run full multi-stage validation suite.

    Returns dict with test results and scores.
    """
    print(f"[free-model-manager] Starting full validation for {model_id}")

    results = {
        "model_id": model_id,
        "timestamp": datetime.utcnow().isoformat(),
        "tests": {},
        "passed_tests": 0,
        "total_tests": 8,
        "overall_pass": False,
    }

    # Update status
    db.update_status(model_id, "INFERENCE_TESTING")

    # Run all tests
    tests = [
        ("TEST_A_BASIC_CODING", validate_basic_coding),
        ("TEST_B_DEBUGGING", validate_debugging),
        ("TEST_C_REPO_REASONING", validate_repo_reasoning),
        ("TEST_D_TDD", validate_tdd),
        ("TEST_E_TOOL_CALLING", validate_tool_calling),
        ("TEST_F_LONG_CONTEXT", validate_long_context),
        ("TEST_G_SELF_CORRECTION", validate_self_correction),
        ("TEST_H_ARCHITECTURE", validate_architecture),
    ]

    for test_name, test_fn in tests:
        try:
            passed, message = test_fn(model_id)
            results["tests"][test_name] = {
                "passed": passed,
                "message": message
            }
            if passed:
                results["passed_tests"] += 1
            print(f"[free-model-manager]   {test_name}: {'✓' if passed else '✗'} {message}")
        except Exception as e:
            results["tests"][test_name] = {
                "passed": False,
                "message": f"Exception: {str(e)}"
            }
            print(f"[free-model-manager]   {test_name}: ✗ Exception: {e}")

    # Determine overall pass
    results["overall_pass"] = results["passed_tests"] >= 6  # Pass if at least 6/8 tests pass

    if results["overall_pass"]:
        db.update_status(model_id, "CODING_TESTING")
    else:
        db.update_status(model_id, "REJECTED", f"Failed {results['total_tests'] - results['passed_tests']}/8 tests")

    return results


def quick_health_check(model_id: str) -> tuple[bool, str, float]:
    """Quick health check using a simple coding prompt.

    Returns: (is_healthy, error_message, latency_ms)
    """
    prompt = "Write a Python function that returns the sum of two numbers. Only code."
    success, response, latency = run_inference(model_id, prompt, timeout=90)

    if not success:
        return False, response or "Health check failed", latency

    if not response.strip():
        return False, "Empty response", latency

    if "def " not in response and "return" not in response.lower():
        return False, "No function found", latency

    return True, "", latency


# Global reference
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
