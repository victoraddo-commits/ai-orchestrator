from pathlib import Path

from core.media_factory import trends

FIXTURE = Path(__file__).parent / "fixtures" / "trends_sample.xml"


def _signals():
    return trends.parse_rss(FIXTURE.read_text(), geo="GH")


def test_parse_traffic_handles_google_formats():
    assert trends.parse_traffic("200+") == 200
    assert trends.parse_traffic("2,000+") == 2000
    assert trends.parse_traffic("10000+") == 10000
    assert trends.parse_traffic(None) is None
    assert trends.parse_traffic("abc") is None


def test_parse_rss_extracts_signal_fields():
    signals = _signals()
    assert [s.title for s in signals] == ["alpha trend", "beta trend", "gamma trend"]
    by_title = {s.title: s for s in signals}
    assert by_title["beta trend"].traffic == 2000
    assert by_title["alpha trend"].news_count == 3
    assert by_title["gamma trend"].news_count == 0
    assert all(s.geo == "GH" for s in signals)


def test_validate_scores_are_derived_and_bounded():
    scored = trends.validate(_signals())
    assert len(scored) == 3
    for entry in scored:
        score = entry["score"]
        assert 0.0 <= score.momentum <= 1.0
        assert 0.0 <= score.competition <= 1.0
        assert 0.0 <= score.shelf_life <= 1.0
        assert 0.0 <= score.score <= 1.0
        assert abs(score.shelf_life - (1.0 - score.competition)) < 1e-9
        assert abs(score.score - (0.5 * score.momentum + 0.5 * score.shelf_life)) < 1e-9


def test_validate_ranks_high_traffic_low_competition_first():
    scored = trends.validate(_signals())
    by_title = {e["signal"].title: e["score"].score for e in scored}
    assert by_title["beta trend"] > by_title["gamma trend"] > by_title["alpha trend"]
    assert max(e["score"].competition for e in scored) == 1.0


def test_validate_empty_returns_empty():
    assert trends.validate([]) == []


def test_validate_without_any_evidence_is_unverified():
    signals = trends.parse_rss(
        "<rss><channel><item><title>bare</title></item></channel></rss>", geo="GH"
    )
    scored = trends.validate(signals)
    assert scored[0]["status"] == "UNVERIFIED"


def test_validate_with_data_is_verified():
    scored = trends.validate(_signals())
    assert all(e["status"] == "VERIFIED" for e in scored)
