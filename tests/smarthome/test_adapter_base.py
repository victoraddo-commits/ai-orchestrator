import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.base import ProviderAdapter, AdapterError


class Fake(ProviderAdapter):
    name = "fake"

    def identify(self):
        return [{"provider_id": "x1", "name": "Fake device"}]

    def get_state(self, provider_id):
        return {"on": True}

    def set_state(self, provider_id, changes):
        if "bad" in changes:
            raise AdapterError("unsupported")
        return {"on": changes.get("on")}


def test_interface_contract():
    a = Fake()
    assert a.name == "fake"
    assert a.identify()[0]["provider_id"] == "x1"
    assert a.get_state("x1") == {"on": True}
    assert a.set_state("x1", {"on": False}) == {"on": False}


def test_errors_are_typed():
    a = Fake()
    try:
        a.set_state("x1", {"bad": 1})
        assert False
    except AdapterError as e:
        assert "unsupported" in str(e)
