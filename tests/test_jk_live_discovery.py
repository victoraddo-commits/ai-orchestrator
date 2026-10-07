import sys
sys.path.insert(0, "/opt/ai-orchestrator/core")
from live_discovery import scan_live_estate

def test_modules_discovered():
    caps = scan_live_estate()
    mods = [c for c in caps if c["module"] == "core"]
    assert len(mods) > 100
    ids = {c["capability_id"] for c in mods}
    assert "core:command_bus" in ids
    assert "core:cerebrum" in ids or any("cerebrum" in i for i in ids)

def test_services_discovered():
    caps = scan_live_estate()
    svcs = [c for c in caps if c["module"] == "services"]
    names = {c["name"] for c in svcs}
    assert "ai-orchestrator-api" in names
    assert "juris-kai" in names

def test_no_bak_noise():
    caps = scan_live_estate()
    assert not any("bak" in c["capability_id"] for c in caps)
