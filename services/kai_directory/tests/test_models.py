from services.kai_directory.models import slugify, Record


def test_slugify_basic():
    assert slugify("Money Center") == "money-center"
    assert slugify("kai_betting") == "kai-betting"
    assert slugify("Directives!!") == "directives"


def test_slugify_collapses_and_trims():
    assert slugify("  a  --  b  ") == "a-b"
    assert slugify("") == ""


def test_record_defaults_and_url():
    r = Record(id="money", name="money", display_name="Money Center",
               category="finances", host="ct108", ip="192.168.1.118", port=8095)
    assert r.target_url == "http://192.168.1.118:8095"
    assert r.tailnet_name == ""
    assert r.health_status == "unknown"


def test_record_to_dict_roundtrip_fields():
    r = Record(id="bet", name="bet", display_name="KAI Bet", category="betting",
               host="ct111", ip="192.168.1.111", port=8000, bind="0.0.0.0")
    d = r.to_dict()
    assert d["id"] == "bet" and d["target_url"] == "http://192.168.1.111:8000"
    assert set(d) >= {"id", "name", "tailnet_name", "health_status", "source"}
