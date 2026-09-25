import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import ups_state as S


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_default_memory_dir", lambda: tmp_path)


def test_normalize_flags():
    assert S.normalize("OL") == S.ONLINE
    assert S.normalize("OB") == S.ON_BATTERY
    assert S.normalize("OB LB") == S.LOW_BATTERY
    assert S.normalize("OL CHRG") == S.CHARGING
    assert S.normalize("OL OVER") == S.OVERLOAD
    assert S.normalize("FSD") == S.SHUTDOWN_PENDING
    assert S.normalize("") == S.UNKNOWN


def test_comm_lost_never_confused_with_utility():
    # A communication failure must NOT look like an outage
    assert S.normalize("OL", reachable=False) == S.COMM_LOST
    assert S.normalize("OB", reachable=False) == S.COMM_LOST


def test_transition_recorded_and_deduped(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    events = []
    m = S.UpsStateMachine(publish=events.append)
    assert m.update("OL") is not None            # UNKNOWN -> ONLINE (a change)
    assert m.update("OL") is None                # no change
    tr = m.update("OB")                          # ONLINE -> ON BATTERY
    assert tr.frm == S.ONLINE and tr.to == S.ON_BATTERY
    log = S.read_events()
    assert [e["to"] for e in log][-1] == S.ON_BATTERY
    assert len(events) == 2                       # two transitions published


def test_full_outage_and_recovery_sequence(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    m = S.UpsStateMachine()
    seq = ["OL", "OB", "OB LB", "OL CHRG", "OL"]
    tos = []
    for f in seq:
        tr = m.update(f)
        if tr:
            tos.append(tr.to)
    assert tos == [S.ONLINE, S.ON_BATTERY, S.LOW_BATTERY, S.CHARGING, S.ONLINE]


def test_duration_tracked(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    m = S.UpsStateMachine()
    m.update("OL")
    m.since -= 120  # pretend 2 minutes passed
    tr = m.update("OB")
    assert tr.duration_s is not None and tr.duration_s >= 100
