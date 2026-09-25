import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.base import AdapterError
from core.smarthome.adapters import tuya as T


def test_normalise_maps_standard_dps():
    assert T.TuyaAdapter._normalise({"dps": {"1": True, "2": 80}}) == {
        "state": "on", "brightness": 80}


def test_identify_lists_configured_devices():
    a = T.TuyaAdapter({"bf123": {"ip": "192.168.1.16", "name": "Lamp",
                                 "local_key": "K"}})
    ids = a.identify()
    assert ids[0]["provider_id"] == "bf123"


def test_control_without_key_is_typed_error():
    a = T.TuyaAdapter({"bf123": {"ip": "192.168.1.16"}})
    try:
        a.set_state("bf123", {"on": True})
        assert False
    except AdapterError as e:
        assert "not configured" in str(e)


def test_no_fabrication_in_normalise():
    # missing DPs => empty state, never invented
    assert T.TuyaAdapter._normalise({"dps": {}}) == {}
