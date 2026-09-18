import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MEDIA_DATA_DIR", "/tmp/kai-media-tests")
os.environ.setdefault("MEDIA_ENABLED", "true")


@pytest.fixture(autouse=True)
def _no_live_vault(monkeypatch):
    """Never touch kai-vault or a provider from unit tests.

    Both provider resolvers default to unconfigured; individual tests opt in
    by re-patching the resolver(s) they exercise.
    """
    from core.media_factory import assets

    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: None)
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: None)
    yield
