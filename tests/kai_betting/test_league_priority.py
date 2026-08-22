"""Covers the marquee-league priority that keeps the top European competitions
in the daily pick pool (they were being silently crowded out of the per-sport
`upcoming[:max_events]` FIFO slice by the minor-league long tail).
"""

from core.kai_betting.data_ingestion import _is_marquee_league, _league_tier


def test_marquee_league_detection_by_slug():
    # The five big European leagues + the two continental cups, in the slug
    # forms observed across providers (hyphen and underscore variants).
    for slug in (
        "epl", "england-premier-league",
        "spain-la-liga", "spain_la_liga",
        "italy-serie-a", "italy_serie_a",
        "germany-bundesliga", "germany_bundesliga",
        "france-ligue-1", "france_ligue_one",
        "champions-league", "uefa-europa-league",
    ):
        assert _is_marquee_league("", slug), f"expected marquee: {slug}"


def test_marquee_league_detection_by_name():
    assert _is_marquee_league("UEFA Champions League, Qualification", "")
    assert _is_marquee_league("UEFA Europa League, Playoff Round", "")


def test_minor_leagues_are_not_marquee():
    for name, slug in (
        ("Armenia - Premier League", "armenia-premier-league"),
        ("Bhutan - Premier League", "bhutan-premier-league"),
        ("Australia - Victoria NPL", "australia-victoria-npl"),
        ("Bulgaria - Vtora Liga", "bulgaria-vtora-liga"),
        ("Estonia - U19 Eliitliiga Esiliiga", "estonia-u19-eliitliiga-esiliiga"),
    ):
        assert not _is_marquee_league(name, slug), f"expected non-marquee: {name}"


def test_top_five_leagues_are_tier_one():
    # The tier filter must keep resolving these to tier 1 (not 99/excluded).
    for name, slug in (
        ("Premier League", "epl"),
        ("England - Premier League", "england-premier-league"),
        ("Spain La Liga", "spain_la_liga"),
        ("Italy Serie A", "italy_serie_a"),
        ("Germany Bundesliga", "germany_bundesliga"),
        ("France - Ligue 1", "france-ligue-1"),
    ):
        assert _league_tier(name, slug) == 1, f"expected tier 1: {name}"
