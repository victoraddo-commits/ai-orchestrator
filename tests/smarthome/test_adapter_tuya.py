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


def test_control_without_key_or_cloud_is_typed_error(monkeypatch):
    """With no local key and no cloud session, control must raise a typed error.

    The cloud fallback is stubbed so the test never touches live credentials.
    """
    a = T.TuyaAdapter({"bf123": {"ip": "169.155.1.2"}})  # not a local IP
    monkeypatch.setattr(a, "_cloud", lambda: None)
    try:
        a.set_state("bf123", {"on": True})
        assert False
    except AdapterError as e:
        assert "not controllable" in str(e)


def test_no_fabrication_in_normalise():
    # missing DPs => empty state, never invented
    assert T.TuyaAdapter._normalise({"dps": {}}) == {}
