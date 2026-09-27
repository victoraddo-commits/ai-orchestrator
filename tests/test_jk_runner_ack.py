"""FIX A regression: after the runner's disclaimer ack, turn-1 must be a
legal answer, not the welcome/disclaimer screen."""
import importlib.util
import os
import sys
import tempfile
import uuid

os.environ.setdefault("JURIS_KAI_DB_DIR", tempfile.mkdtemp(prefix="jk_ack_"))
sys.path.insert(0, "/opt/ai-orchestrator")

import core.juris_kai.bot as bot  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "juris_golden_runner",
        "/opt/ai-orchestrator/tests/juris_golden/runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ack_makes_first_turn_a_legal_answer():
    runner = _load_runner()
    uid = str(3_000_000_000 + (uuid.uuid4().int % 900_000_000))
    driver = runner.BotDriver(real=False)
    try:
        driver.prepare(uid)
        reply = driver.send(
            uid,
            "What are the duties of directors under the Companies Act, 2019?")
    finally:
        driver.restore()
    text = reply.get("text") or ""
    assert len(text) > 100, f"turn-1 reply too short to be a legal answer: {text[:80]!r}"
    assert "Welcome to Juris Kai" not in text, "turn-1 still shows the welcome screen"
    assert "disclaimer" not in text.lower(), "turn-1 still shows the disclaimer"
