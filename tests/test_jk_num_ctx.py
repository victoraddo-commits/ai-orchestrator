"""TDD regression: env-gated num_ctx and per-task reasoning temperature.

FIX 2: ``JURIS_KAI_NUM_CTX`` raises the model context window (default 0 =
leave ollama's own default untouched) and must surface as
``options["num_ctx"]`` in the ollama payload.
FIX 3: the three deep reasoning passes run at ``JURIS_KAI_TEMPERATURE_REASONING``
(default 0.15) while chat keeps ``JURIS_KAI_TEMPERATURE`` (0.7).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import streaming  # noqa: E402


def test_num_ctx_options_seam_when_set(monkeypatch):
    monkeypatch.setattr(streaming, "NUM_CTX", 16384)
    opts = streaming._build_options("juris_judge", reasoning=True)
    assert opts["num_ctx"] == 16384
    assert opts["temperature"] == streaming.TEMP_REASONING


def test_num_ctx_options_seam_default_absent(monkeypatch):
    monkeypatch.setattr(streaming, "NUM_CTX", 0)
    opts = streaming._build_options("legal_research", reasoning=False)
    assert "num_ctx" not in opts
    assert opts["temperature"] == streaming.TEMPERATURE


def test_num_ctx_env_var_parsed(monkeypatch):
    """Constant parsing mirrors the token-budget env pattern (subprocess
    reimport, since the constant is read at module import time)."""
    monkeypatch.setenv("JURIS_KAI_NUM_CTX", "16384")
    code = (
        "import sys; sys.path.insert(0, '/opt/ai-orchestrator'); "
        "from core.juris_kai import streaming; print(streaming.NUM_CTX)")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True,
                         env={**os.environ})
    assert out.stdout.strip() == "16384", out.stderr


def test_num_ctx_default_is_zero(monkeypatch):
    env = {**os.environ}
    env.pop("JURIS_KAI_NUM_CTX", None)
    code = (
        "import sys; sys.path.insert(0, '/opt/ai-orchestrator'); "
        "from core.juris_kai import streaming; print(streaming.NUM_CTX)")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "0", out.stderr


def test_reasoning_temperature_default_low(monkeypatch):
    env = {**os.environ}
    env.pop("JURIS_KAI_TEMPERATURE_REASONING", None)
    code = (
        "import sys; sys.path.insert(0, '/opt/ai-orchestrator'); "
        "from core.juris_kai import streaming; print(streaming.TEMP_REASONING)")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "0.15", out.stderr


def test_deep_passes_dispatch_reasoning_true(monkeypatch):
    """The three deep passes must thread reasoning=True through the seam."""
    captured = {}

    def fake_generate(prompt, task_type=None, deep=False, reasoning=False,
                      **kw):
        captured["generate"] = (task_type, deep, reasoning)
        return "ok"

    def fake_stream(prompt, task_type=None, deep=False, reasoning=False,
                    **kw):
        captured["stream"] = (task_type, deep, reasoning)
        return iter(["ok"])

    import core.juris_kai.reasoning as reasoning
    monkeypatch.setattr(streaming, "generate", fake_generate)
    monkeypatch.setattr(streaming, "stream_chat", fake_stream)
    reasoning._generate("p", "juris_advocate")
    reasoning._stream_generate("p", "juris_judge")
    for key in ("generate", "stream"):
        assert captured[key] == ("juris_advocate" if key == "generate"
                                 else "juris_judge", True, True), captured


def test_chat_generation_keeps_chat_temperature(monkeypatch):
    """Chat (reasoning=False) must keep JURIS_KAI_TEMPERATURE, not TEMP_REASONING."""
    monkeypatch.setattr(streaming, "TEMPERATURE", 0.7)
    monkeypatch.setattr(streaming, "TEMP_REASONING", 0.15)
    assert streaming._build_options("legal_research",
                                    reasoning=False)["temperature"] == 0.7
    assert streaming._build_options("legal_research",
                                    reasoning=True)["temperature"] == 0.15
