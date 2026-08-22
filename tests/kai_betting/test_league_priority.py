"""Covers the approved sports & competition scope registry (the hard filter).

Verifies the acceptance/rejection matrix from the scope-reset spec: only
football/tennis/basketball are active; football is limited to the top-5
leagues × 2 divisions + major tournaments; tennis and basketball are limited
to elite competitions; everything else is rejected with a stable reason.
"""

import pytest

from core.kai_betting import scope
from core.kai_betting.data_ingestion import _league_tier


# ── Sport filter ──────────────────────────────────────────────────────────────

def test_approved_sports_only():
    assert scope.APPROVED_SPORTS == {"football", "tennis", "basketball"}
    for s in ("football", "tennis", "basketball"):
        assert scope.sport_allowed(s)
    for s in ("ice_hockey", "baseball", "american_football", "cricket",
              "boxing", "mma", "volleyball", "rugby", "darts", "golf", "esports"):
        assert not scope.sport_allowed(s)


def test_out_of_scope_sport_rejected():
    c = scope.classify_competition("ice_hockey", "NHL", "nhl")
    assert not c.allowed
    assert c.reason == "OUT_OF_SCOPE_SPORT"


# ── Football: accepted competitions ───────────────────────────────────────────

@pytest.mark.parametrize("slug", [
    "epl", "england-premier-league",
    "efl-championship", "championship",
    "spain-la-liga", "spain_la_liga",
    "spain-segunda-division", "segunda-division",
    "italy-serie-a", "italy_serie_a",
    "italy-serie-b", "serie-b",
    "germany-bundesliga", "germany_bundesliga",
    "germany-2-bundesliga", "2-bundesliga",
    "france-ligue-1", "france-ligue-one",
    "france-ligue-2", "ligue-2",
])
def test_football_approved_leagues_accepted(slug):
    c = scope.classify_competition("football", "", slug)
    assert c.allowed, f"expected accepted: {slug}"


@pytest.mark.parametrize("name", [
    "UEFA Champions League", "UEFA Champions League, Qualification",
    "UEFA Europa League", "UEFA Europa League, Playoff Round",
    "UEFA Conference League", "FIFA Club World Cup",
    "FIFA World Cup", "UEFA European Championship", "Copa América",
    "Africa Cup of Nations", "AFCON", "UEFA Nations League",
])
def test_football_tournaments_accepted(name):
    c = scope.classify_competition("football", name, "")
    assert c.allowed, f"expected accepted tournament: {name}"


# ── Football: rejected competitions ───────────────────────────────────────────

@pytest.mark.parametrize("name,slug", [
    ("England - League One", "england-league-one"),
    ("Italy - Serie C", "italy-serie-c"),
    ("Germany - 3. Liga", "germany-3-liga"),
    ("Spain - Primera Federación", "spain-primera-federacion"),
    ("France - National", "france-national"),
    ("USA - MLS", "usa-mls"),
    ("Saudi Pro League", "saudi-pro-league"),
    ("Brazil - Brasileiro Serie A", "brazil-brasileiro-serie-a"),
    ("Armenia - Premier League", "armenia-premier-league"),
    ("Bhutan - Premier League", "bhutan-premier-league"),
    ("Australia - Victoria NPL", "australia-victoria-npl"),
])
def test_football_out_of_scope_rejected(name, slug):
    c = scope.classify_competition("football", name, slug)
    assert not c.allowed, f"expected rejected: {name}"
    assert c.reason == "OUT_OF_SCOPE_COMPETITION"


# ── Tennis ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", [
    "australian-open", "roland-garros", "wimbledon", "us-open",
    "atp-finals", "wta-finals", "atp-masters-1000", "wta-1000",
    "atp-500", "wta-500",
])
def test_tennis_elite_accepted(slug):
    c = scope.classify_competition("tennis", "", slug)
    assert c.allowed, f"expected accepted: {slug}"


@pytest.mark.parametrize("name,slug", [
    ("ITF - Tianjin", "itf-tianjin"),
    ("Challenger - Astana", "challenger-astana"),
    ("ATP Challenger", "atp-challenger"),
    ("Junior - Wimbledon", "junior-wimbledon"),
])
def test_tennis_low_level_rejected(name, slug):
    c = scope.classify_competition("tennis", name, slug)
    assert not c.allowed, f"expected rejected: {name}"


# ── Basketball ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", [
    "nba", "euroleague", "acb", "liga-endesa", "lega-basket-serie-a",
    "basketball-bundesliga", "lnb-pro-a", "turkish-basketball-super-league",
    "greek-basket-league", "wnba", "nbl", "cba",
])
def test_basketball_elite_accepted(slug):
    c = scope.classify_competition("basketball", "", slug)
    assert c.allowed, f"expected accepted: {slug}"


def test_basketball_college_rejected():
    c = scope.classify_competition("basketball", "USA - NCAA", "usa-ncaa")
    assert not c.allowed


# ── DB tier wrapper (backward compat) ─────────────────────────────────────────

def test_league_tier_wrapper_maps_top_five_to_tier_one():
    for name, slug in (
        ("Premier League", "epl"),
        ("Spain La Liga", "spain_la_liga"),
        ("Italy Serie A", "italy_serie_a"),
        ("Germany Bundesliga", "germany_bundesliga"),
        ("France - Ligue 1", "france-ligue-1"),
    ):
        assert _league_tier(name, slug) == 1, f"expected tier 1: {name}"


def test_league_tier_wrapper_second_divisions_are_tier_two():
    for name, slug in (
        ("England - Championship", "efl-championship"),
        ("Spain - Segunda Division", "segunda-division"),
        ("Italy - Serie B", "serie-b"),
    ):
        assert _league_tier(name, slug) == 2, f"expected tier 2: {name}"


def test_league_tier_wrapper_out_of_scope_is_none():
    assert _league_tier("USA - MLS", "usa-mls") is None
    assert _league_tier("ITF - Tianjin", "itf-tianjin") is None
