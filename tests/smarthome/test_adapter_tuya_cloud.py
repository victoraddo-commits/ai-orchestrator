import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.base import AdapterError
from core.smarthome.adapters import tuya_cloud as TC


class FakeMgr:
    device_map = {}

    def update_device_cache(self):
        class D:
            online = True
            product_name = "Test"
        self.device_map = {"dev1": D()}

    def send_commands(self, device_id, commands):
        FakeMgr.sent = (device_id, commands)


class FakeDev:
    online = False


def test_requires_session():
    a = TC.TuyaCloudAdapter(session={})
    try:
        a.identify()
        assert False
    except AdapterError as e:
        assert "not configured" in str(e)


def test_control_builds_switch_dp():
    a = TC.TuyaCloudAdapter(session={"access_token": "t", "uid": "u",
                                     "terminal_id": "tid", "endpoint": "ep"})
    a._manager = FakeMgr()
    a.set_state("dev1", {"on": True})
    assert FakeMgr.sent == ("dev1", [{"code": "switch_1", "value": True}])


def test_no_fields_errors():
    a = TC.TuyaCloudAdapter(session={"access_token": "t", "uid": "u",
                                     "terminal_id": "tid", "endpoint": "ep"})
    a._manager = FakeMgr()
    try:
        a.set_state("dev1", {})
        assert False
    except AdapterError as e:
        assert "no controllable" in str(e)
