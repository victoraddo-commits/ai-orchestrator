import pytest, tempfile, os
from pathlib import Path

_test_dir = tempfile.mkdtemp()
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = _test_dir

from core.ecosystem_duplication import find_duplications, generate_duplication_report

def test_find_duplications_detects_telegram_overlap():
    from core.ecosystem_graph import save_graph
    save_graph({
        "entities": {
            "kai-notify": {"id": "kai-notify", "type": "service", "name": "KAI Notify", "status": "active"},
            "telegra-approval-responder": {"id": "telegra-approval-responder", "type": "service", "name": "Telegra Approval Responder", "status": "active"},
            "kai-audit": {"id": "kai-audit", "type": "service", "name": "KAI Audit", "status": "active"},
        },
        "capabilities": {
            "telegram-messaging": {"id": "telegram-messaging", "canonical_owner": "kai-notify", "status": "active"},
        },
        "relationships": [
            {"from": "kai-notify", "to": "telegram-messaging", "type": "notifies"},
            {"from": "telegra-approval-responder", "to": "telegram-messaging", "type": "notifies"},
            {"from": "kai-audit", "to": "telegram-messaging", "type": "notifies"},
        ],
        "last_updated": None,
    })
    dupes = find_duplications()
    telegram_dupes = [d for d in dupes if "telegram" in d.get("id", "")]
    assert len(telegram_dupes) >= 1

def test_generate_report_contains_key_sections():
    from core.ecosystem_graph import save_graph
    save_graph({
        "entities": {
            "kai-notify": {"id": "kai-notify", "type": "service", "name": "KAI Notify", "status": "active"},
            "telegra-approval-responder": {"id": "telegra-approval-responder", "type": "service", "name": "Telegra", "status": "active"},
        },
        "capabilities": {},
        "relationships": [
            {"from": "kai-notify", "to": "telegram-capability", "type": "notifies"},
            {"from": "telegra-approval-responder", "to": "telegram-capability", "type": "notifies"},
            {"from": "kai-notify", "to": "kai-vault", "type": "auth_with"},
        ],
        "last_updated": None,
    })
    report = generate_duplication_report()
    assert "# KAI Ecosystem Duplication Report" in report
    assert "## Summary" in report
    assert "kai-notify" in report
