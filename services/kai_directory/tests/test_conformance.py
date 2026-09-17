from services.kai_directory.models import Record
from services.kai_directory.conformance import check


def test_check_reports_missing():
    discovered = [Record(id="a", name="a"), Record(id="b", name="b")]
    stored = [Record(id="a", name="a")]
    report = check(discovered, stored)
    assert report["ok"] is False
    assert report["missing"] == ["b"]


def test_check_passes_when_covered():
    discovered = [Record(id="a", name="a")]
    stored = [Record(id="a", name="a")]
    assert check(discovered, stored)["ok"] is True
