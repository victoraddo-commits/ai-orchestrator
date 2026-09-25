import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import registry as R
from core.smarthome import rooms as Rooms
from core.smarthome import service as S
from core.smarthome.models import DeviceCreate, DeviceKind, Room


class FakeAdapter:
    name = "fake"

    def identify(self):
        return [{"provider_id": "x1", "name": "Fake"}]

    def get_state(self, provider_id):
        return {"state": "on", "brightness": 50}

    def set_state(self, provider_id, changes):
        return {"state": "on" if changes.get("on") else "off",
                "brightness": changes.get("brightness", 50)}


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)
    monkeypatch.setattr(Rooms, "_default_memory_dir", lambda: tmp_path)


def test_refresh_pulls_state(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    R.upsert(DeviceCreate(name="Lamp", provider="fake", provider_id="x1",
                          kind=DeviceKind.LIGHT))
    S.register_adapter("fake", FakeAdapter())
    out = S.refresh("fake")
    assert out["refreshed"] == 1
    d = R.list_devices()[0]
    assert d.state["state"] == "on" and d.state_fresh is True


def test_control_verifies_readback(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="Lamp", provider="fake", provider_id="x1",
                                kind=DeviceKind.LIGHT))
    S.register_adapter("fake", FakeAdapter())
    res = S.control(rec.id, {"on": False})
    assert res["observed"]["state"] == "off"
    assert res["verified"] is True


def test_control_unverified_when_state_mismatch(monkeypatch, tmp_path):
    class Stubborn(FakeAdapter):
        def set_state(self, provider_id, changes):
            return {"state": "on"}  # ignores the request

    _tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="Lamp", provider="fake", provider_id="x1",
                                kind=DeviceKind.LIGHT))
    S.register_adapter("fake", Stubborn())
    res = S.control(rec.id, {"on": False})
    assert res["verified"] is False


def test_device_view_marks_stale(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    R.upsert(DeviceCreate(name="Lamp", provider="fake", provider_id="x1"))
    d = R.list_devices()[0].to_dict()
    d["last_seen"] = "2000-01-01T00:00:00+00:00"
    assert S.device_view(d)["stale"] is True


def test_rooms_group_and_unassigned(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    Rooms.upsert_room(Room(id="living", name="Living room"))
    a = R.upsert(DeviceCreate(name="A", provider="fake", provider_id="a", room="living"))
    b = R.upsert(DeviceCreate(name="B", provider="fake", provider_id="b"))
    grouped = Rooms.group_by_room([a, b])
    assert "living" in grouped and "__unassigned" in grouped
