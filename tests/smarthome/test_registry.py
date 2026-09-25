import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import registry as R
from core.smarthome.models import DeviceCreate, DeviceKind


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)


def test_upsert_creates_then_updates_by_provider_key(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="Lamp", provider="tuya", provider_id="a1",
                                kind=DeviceKind.LIGHT, address="192.168.1.16"))
    assert rec.id.startswith("smd_")
    again = R.upsert(DeviceCreate(name="Lamp 2", provider="tuya", provider_id="a1"))
    assert again.id == rec.id and again.name == "Lamp 2"
    assert len(R.list_devices()) == 1


def test_get_update_delete_and_unknown(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="S", provider="ha", provider_id="switch.x"))
    assert R.get(rec.id).name == "S"
    assert R.get("nope") is None
    R.set_state(rec.id, {"on": True})
    assert R.get(rec.id).state == {"on": True}
    R.delete(rec.id)
    assert R.get(rec.id) is None


def test_hooks_fire_on_create(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    seen = []
    R.register_hook("on_create", lambda rec: seen.append(rec.id))
    R.upsert(DeviceCreate(name="X", provider="tuya", provider_id="z"))
    assert len(seen) == 1
