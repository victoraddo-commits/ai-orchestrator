import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import discovery as D


def test_parse_ssdp_notify():
    pkt = ("NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           "NT: urn:schemas-upnp-org:device:Basic:1\r\n"
           "SERVER: Tuya/1.0 UPnP/1.0\r\nLOCATION: http://192.168.1.16:6668/x.xml\r\n"
           "USN: uuid:abc::urn:schemas-upnp-org:device:Basic:1\r\n\r\n")
    info = D.parse_ssdp(pkt.encode())
    assert info["ip"] == "192.168.1.16"


def test_parse_tuya_broadcast():
    data = {"ip": "192.168.1.16", "gwId": "bf123", "productKey": "abc"}
    info = D.parse_tuya(data)
    assert info == {"ip": "192.168.1.16", "provider_id": "bf123", "vendor": "Tuya Smart"}


def test_fingerprint_port_map():
    assert D.classify_ports({6668}) == ("tuya", "Tuya Smart")
    assert D.classify_ports({8123}) == ("homeassistant", None)
    assert D.classify_ports({1883}) == ("mqtt", None)
    assert D.classify_ports({22, 80}) == (None, None)


def test_candidate_shape():
    c = D.Candidate(ip="192.168.1.16", mac="50:8a:06:6a:3c:36", provider="tuya",
                    provider_id="bf123", vendor="Tuya Smart", ports=[6668])
    assert c.to_dedup_key() == "tuya:bf123"
