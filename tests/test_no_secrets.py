"""Guard against hardcoded secrets (esp. Telegram bot tokens) in tracked files.

These tests pin both halves of the guard:

* unit coverage for the scanner's detection / masking / allowlist logic, and
* an end-to-end assertion that the repository's *currently tracked* files
  contain no unallowlisted secrets. That end-to-end case failed while real
  Telegram tokens were committed (see docs/JURIS_KAI_TOKEN_ROTATION_GUIDE.md),
  so it is the regression guard for this incident.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER_PATH = REPO_ROOT / "scripts" / "check_no_secrets.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("check_no_secrets", SCANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses needs the module registered while it executes.
    sys.modules["check_no_secrets"] = module
    spec.loader.exec_module(module)
    return module


scanner = _load_scanner()

# A realistic-shaped but entirely synthetic token (bot id 9876543210). Built
# from fragments so this test file itself contains no token-shaped literal.
SYNTHETIC_TOKEN = "9876543210:" + "AAH" + "f" * 32
# The deliberately fake token the telegram-bridge tests use; allowlisted.
ALLOWLISTED_FAKE_TOKEN = "123456789:AABB" + "C" * 31
# A synthetic OpenAI-style project key.
SYNTHETIC_OPENAI_KEY = "sk-proj-" + "a" * 40


def test_detects_a_hardcoded_telegram_bot_token():
    findings = scanner.find_secrets(f'token = "{SYNTHETIC_TOKEN}"', "sample.py")

    assert len(findings) == 1
    assert findings[0].rule == "telegram_bot_token"
    assert findings[0].filename == "sample.py"
    assert findings[0].line == 1


def test_allowlists_the_deliberately_fake_test_token():
    assert scanner.find_secrets(
        f'token = "{ALLOWLISTED_FAKE_TOKEN}"', "tests/test_telegram_bridge.py"
    ) == []


def test_ignores_short_or_non_token_text():
    text = "\n".join(
        [
            "chat_id: 12345",
            "ratio 123456789:too-short",
            "url https://api.telegram.org/bot/getUpdates",
        ]
    )
    assert scanner.find_secrets(text, "sample.py") == []


def test_detects_other_high_signal_keys():
    findings = scanner.find_secrets(f"key = {SYNTHETIC_OPENAI_KEY}", "sample.py")

    assert [f.rule for f in findings] == ["openai_project_key"]


def test_findings_never_contain_the_raw_secret():
    findings = scanner.find_secrets(f"x = {SYNTHETIC_TOKEN}", "sample.py")

    assert findings
    rendered = " ".join(str(f) for f in findings)
    assert SYNTHETIC_TOKEN not in rendered
    # The bot id is not itself a secret and is useful for triage.
    assert "9876543210" in findings[0].masked_secret


def test_reports_correct_line_numbers():
    text = "line one\nline two\n" + f"boom = {SYNTHETIC_TOKEN}\n"
    findings = scanner.find_secrets(text, "sample.py")

    assert findings[0].line == 3


def test_repository_tracked_files_have_no_unallowlisted_secrets():
    findings = scanner.scan_repository(REPO_ROOT)

    assert findings == [], (
        "Hardcoded secrets found in tracked files (values masked):\n"
        + "\n".join(str(f) for f in findings)
    )


@pytest.mark.parametrize("path", ["scripts/check_no_secrets.py"])
def test_scanner_is_itself_committed(path):
    assert (REPO_ROOT / path).is_file()
