"""test-gen configuration."""

import os
from pathlib import Path

DEFAULT_GENERATED_TEST_DIR = os.environ.get(
    "TEST_GEN_OUTPUT_DIR",
    str(Path(__file__).parent.parent.parent / "tests" / "generated"),
)

DEFAULT_CODING_PROVIDER = os.environ.get("TEST_GEN_CODING_PROVIDER", "qwen4_coding")

DEFAULT_TIMEOUT = int(os.environ.get("TEST_GEN_TIMEOUT", "300"))

DEFAULT_TEST_RUNNER = os.environ.get("TEST_GEN_RUNNER", "pytest")

DEFAULT_TEST_FLAGS = os.environ.get("TEST_GEN_RUNNER_FLAGS", "-xvs").split()
