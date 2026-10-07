import sys
sys.path.insert(0, "/opt/ai-orchestrator/core")
from live_dupes import find_dupes, find_orphans

def test_dupes_found():
    d = find_dupes()
    assert isinstance(d, list)
    assert all(set(x) >= {"cluster", "members"} for x in d)

def test_noisy_but_bounded():
    d = find_dupes()
    assert 0 <= len(d) <= 50

def test_orphans():
    o = find_orphans()
    assert isinstance(o, list)
    assert all(x.startswith("core:") for x in o)
