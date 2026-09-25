import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.smarthome.adapters.base import AdapterError
from core.smarthome.adapters import ewelink as E

class Fake(E.EWeLinkAdapter):
    def __init__(self): super().__init__(session={"at":"t","appid":"a","region":"eu"})
    def _req(self, method, path, body=None):
        if path.startswith("/v2/device/thing?"):
            return {"error":0,"data":{"thingList":[{"itemData":{"deviceid":"d1","name":"Lamp","online":True,"params":{"switch":"on"}}}]}}
        if path=="/v2/device/thing/status":
            Fake.last=body
            return {"error":0,"data":{}}
        return {"error":404}

def test_identify_and_state():
    a=Fake(); assert a.identify()[0]["provider_id"]=="d1"
    assert a.get_state("d1")["state"]=="on"

def test_control_builds_switch_and_reads_back():
    a=Fake(); out=a.set_state("d1",{"on":False})
    assert Fake.last["params"]=={"switch":"off"} and Fake.last["type"]==1

def test_missing_session_errors():
    a=E.EWeLinkAdapter(session={})
    try: a.identify(); assert False
    except AdapterError as e: assert "not configured" in str(e)
