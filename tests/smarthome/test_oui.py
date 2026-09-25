import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import oui


def test_known_vendors():
    assert oui.vendor_for("50:8a:06:6a:3c:36") == "Tuya Smart"
    assert oui.vendor_for("50-8A-06-00-00-01") == "Tuya Smart"
    assert oui.vendor_for("B4:E6:2D:11:22:33") == "Espressif"


def test_unknown_vendor_is_none():
    assert oui.vendor_for("aa:bb:cc:dd:ee:ff") is None
    assert oui.vendor_for("") is None


def test_prefix_normalisation():
    assert oui.prefix("508A066A3C36") == "50:8A:06"
