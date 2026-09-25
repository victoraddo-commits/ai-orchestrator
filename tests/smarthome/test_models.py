import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.models import DeviceCreate, DeviceRecord, Room, Capability, DeviceKind


def test_device_create_defaults_and_unknown_kind():
    d = DeviceCreate(name="Living room lamp", provider="tuya", provider_id="bf123")
    assert d.kind == DeviceKind.UNKNOWN
    assert d.room is None
    assert d.capabilities == []


def test_device_record_roundtrip_and_stable_id():
    d = DeviceRecord.new(
        name="Front door", provider="tuya", provider_id="bf999",
        kind=DeviceKind.LOCK, capabilities=[Capability.LOCK, Capability.BATTERY],
        room="Entrance", address="192.168.1.16", mac="50:8a:06:6a:3c:36",
    )
    assert d.id.startswith("smd_")
    blob = d.to_dict()
    back = DeviceRecord.from_dict(blob)
    assert back == d


def test_room_word_and_unknown_state_preserved():
    r = Room(id="entrance", name="Entrance", area="Ground floor")
    assert r.to_dict()["name"] == "Entrance"
    d = DeviceRecord.new(name="x", provider="ha", provider_id="light.x")
    assert d.state == {}
    assert d.state_fresh is False
