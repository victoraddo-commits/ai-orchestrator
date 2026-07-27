from core.execution_audit import record, history


def test_record_appends_entry_with_timestamp():
    entry = record({"operator": "alice", "action": "restart_container", "service": "svc-a", "result": "success"})

    assert entry["operator"] == "alice"
    assert "timestamp" in entry


def test_history_returns_all_recorded_entries_in_order():
    record({"operator": "alice", "action": "restart_container", "service": "svc-a", "result": "success"})
    record({"operator": "system(autonomous)", "action": "restart_container", "service": "svc-b", "result": "failed"})

    entries = history()

    assert len(entries) == 2
    assert entries[0]["service"] == "svc-a"
    assert entries[1]["operator"] == "system(autonomous)"
