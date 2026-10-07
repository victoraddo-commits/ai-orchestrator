import sys
sys.path.insert(0, "/opt/ai-orchestrator/core")
from live_discovery import scan_live_estate

def test_purpose_enriched():
    caps = scan_live_estate()
    assert all("purpose" in c for c in caps)
    core = {c["capability_id"]: c for c in caps if c["module"] == "core"}
    cb = core.get("core:command_bus", {})
    assert cb.get("purpose") or cb.get("purpose") == ""

def test_purpose_short():
    caps = scan_live_estate()
    for c in caps:
        if c["module"] == "core" and c["purpose"]:
            assert len(c["purpose"]) <= 120
