"""Regression: HA set_state must poll the read-back after a service call.

Root cause (2026-09-25): HA updates its state machine a beat after the service
call returns. A single immediate read returned the OLD state, so KAI reported
`verified: False` for commands that actually succeeded. set_state now settles.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.homeassistant import HomeAssistantAdapter


class FakeResp:
    status_code = 200

    def json(self):
        return {}


class FakeSession:
    """State flips only after the first read-back (simulating HA lag)."""

    def __init__(self):
        self.state = "on"
        self.reads = 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.state = "on" if json.get("entity_id") and "turn_on" in url else "off"
        self.reads = 0  # lag: state not visible yet
        return FakeResp()

    def get(self, url, headers=None, timeout=None):
        self.reads += 1
        visible = "off" if self.reads > 1 else "on"  # first read still stale
        return FakeResp2(visible)


class FakeResp2:
    status_code = 200

    def __init__(self, state):
        self._s = state

    def json(self):
        return {"state": self._s, "attributes": {"friendly_name": "X"}}


def test_set_state_settles_and_reports_new_state():
    a = HomeAssistantAdapter(base_url="http://x", token="t", session=FakeSession())
    a.session.state = "on"
    out = a.set_state("switch.office_switch_1", {"on": False})
    assert out["state"] == "off", "read-back must reflect the settled new state"


def test_set_state_returns_last_when_never_settles():
    class Stubborn(FakeSession):
        def get(self, url, headers=None, timeout=None):
            return FakeResp2("on")  # never changes

    a = HomeAssistantAdapter(base_url="http://x", token="t", session=Stubborn())
    out = a.set_state("switch.x", {"on": False})
    assert out["state"] == "on"  # honest: reports what it observed
