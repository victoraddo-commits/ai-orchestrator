import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import automations as A


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "_default_memory_dir", lambda: tmp_path)


def test_add_list_get_delete(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    a = A.add(A.Automation.new("Night", {"type": "time", "at": "22:00"},
                               actions=[{"device_id": "d1", "changes": {"on": False}}]))
    assert A.get(a.id).name == "Night"
    assert A.set_enabled(a.id, False).enabled is False
    assert A.delete(a.id) is True
    assert A.get(a.id) is None


def test_trigger_match_state():
    t = {"type": "state", "device_id": "d1", "to": "on"}
    assert A.trigger_matches(t, {"type": "state", "device_id": "d1", "to": "on"})
    assert not A.trigger_matches(t, {"type": "state", "device_id": "d1", "to": "off"})
    assert not A.trigger_matches(t, {"type": "state", "device_id": "d2", "to": "on"})
    assert A.trigger_matches({"type": "state", "to": "any"},
                             {"type": "state", "device_id": "x", "to": "off"})


def test_conditions_fail_closed():
    states = {"d1": {"state": "on", "brightness": 10}}
    assert A.conditions_hold([{"device_id": "d1", "value": "on"}],
                             lambda d: states.get(d))
    assert not A.conditions_hold([{"device_id": "d1", "field": "brightness",
                                   "op": "gt", "value": 50}],
                                 lambda d: states.get(d))
    assert not A.conditions_hold([{"device_id": "missing", "value": "on"}],
                                 lambda d: states.get(d))


def test_run_dispatches_actions_and_records(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    calls = []
    a = A.add(A.Automation.new("Turn on", {"type": "state", "device_id": "d1", "to": "on"},
                               actions=[{"device_id": "d2", "changes": {"on": True}}]))
    rep = A.run(a, lambda d: {"state": "on"}, lambda act: calls.append(act) or {"ok": True})
    assert rep["ran"] and calls
    assert A.get(a.id).run_count == 1


def test_dispatch_event_only_matching(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    A.add(A.Automation.new("A", {"type": "state", "device_id": "d1", "to": "on"},
                           actions=[{"device_id": "d2", "changes": {"on": True}}]))
    A.add(A.Automation.new("B", {"type": "state", "device_id": "zzz", "to": "on"},
                           actions=[{"device_id": "d2", "changes": {"on": True}}]))
    out = A.dispatch_event({"type": "state", "device_id": "d1", "to": "on"},
                           lambda d: {"state": "on"}, lambda act: {"ok": True})
    assert len(out) == 1
