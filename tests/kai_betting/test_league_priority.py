"""Covers the approved sports & competition scope registry (the hard filter).

Verifies the acceptance/rejection matrix from the scope-reset spec, widened to
the full multi-sport universe (2026-09-25): every approved sport carries an
explicit elite-competition whitelist; football keeps its top-5 leagues × 2
divisions + tournaments; tennis/basketball keep their elite tiers; the other
major sports (ice hockey, baseball, American football, rugby, volleyball,
handball, cricket, esports, darts, MMA, boxing, table tennis, snooker, horse
racing) are accepted only inside their whitelists; everything else is rejected
with a stable reason.
"""

import pytest

from core.kai_betting import scope
from core.kai_betting.data_ingestion import _league_tier


EXPANDED_SPORTS = {
    "football", "tennis", "basketball",
    "ice_hockey", "baseball", "american_football",
    "rugby", "volleyball", "handball", "cricket",
    "esports", "darts", "mma", "boxing", "table_tennis", "snooker",
    "horse_racing",
}


# ── Sport filter ──────────────────────────────────────────────────────────────

def test_approved_sports_only():
    assert scope.APPROVED_SPORTS == EXPANDED_SPORTS
    for s in EXPANDED_SPORTS:
        assert scope.sport_allowed(s)
    for s in ("golf", "cycling", "athletics", "formula1", "water_polo"):
        assert not scope.sport_allowed(s), f"expected out of scope: {s}"


def test_out_of_scope_sport_rejected():
    c = scope.classify_competition("golf", "The Open", "the-open")
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
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Conference League",
    "FIFA Club World Cup",
    "FIFA World Cup",
    "UEFA European Championship",
    "Copa América",
    "Africa Cup of Nations", "AFCON",
    "UEFA Nations League",
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


@pytest.mark.parametrize("name", [
    # Non-UEFA "Champions League" confederations must NOT leak in.
    "AFC Champions League",
    "CAF Champions League",
    "OFC Champions League",
    "International Clubs - AFC Champions League Elite, Qualification",
    # Qualifiers / playoffs / preliminary rounds are out of scope.
    "UEFA Champions League, Qualification",
    "UEFA Europa League, Playoff Round",
    "FIBA World Cup, African Qualifiers",
    "EuroBasket, Pre-Qualifiers",
])
def test_non_uefa_tournaments_and_qualifiers_rejected(name):
    c = scope.classify_competition("football", name, "")
    assert not c.allowed, f"expected rejected (football): {name}"
    c = scope.classify_competition("basketball", name, "")
    assert not c.allowed, f"expected rejected (basketball): {name}"


# ── Tennis ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", [
    "australian-open", "roland-garros", "wimbledon", "us-open",
    "atp-finals", "wta-finals", "atp-masters-1000", "wta-1000",
    "atp-500", "wta-500",
])
def test_tennis_elite_accepted(slug):
    c = scope.classify_competition("tennis", "", slug)
    assert c.allowed, f"expected accepted: {slug}"


@pytest.mark.parametrize("name", [
    "ATP - Cincinnati, USA",
    "ATP - Montreal, Canada",
    "WTA - Cincinnati, USA",
    "ATP - Winston Salem, USA",
])
def test_tennis_elite_city_named_accepted(name):
    c = scope.classify_competition("tennis", name, "")
    assert c.allowed, f"expected accepted: {name}"


@pytest.mark.parametrize("name,slug", [
    ("ITF - Tianjin", "itf-tianjin"),
    ("Challenger - Astana", "challenger-astana"),
    ("ATP Challenger", "atp-challenger"),
    ("Junior - Wimbledon", "junior-wimbledon"),
    ("WTA 125K - Philadelphia, USA", "wta-125k-philadelphia"),
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


# ── Newly-approved sports: elite whitelists accepted ──────────────────────────

@pytest.mark.parametrize("sport,slug", [
    ("ice_hockey", "nhl"),
    ("baseball", "mlb"),
    ("american_football", "nfl"),
    ("rugby", "six-nations"),
    ("volleyball", "cev-champions-league"),
    ("handball", "ehf-champions-league"),
    ("cricket", "ipl"),
    ("esports", "league-of-legends"),
    ("darts", "pdc-world-championship"),
    ("mma", "ufc"),
    ("boxing", "wbc"),
    ("table_tennis", "wtt"),
    ("snooker", "world-snooker-championship"),
    ("horse_racing", "cheltenham"),
])
def test_new_sports_elite_leagues_accepted(sport, slug):
    c = scope.classify_competition(sport, "", slug)
    assert c.allowed, f"expected accepted: {sport}/{slug}"
    assert c.tier  # non-empty tier code
    assert c.priority > 0


@pytest.mark.parametrize("sport,name", [
    ("ice_hockey", "NHL"),
    ("baseball", "MLB"),
    ("american_football", "NFL"),
    ("rugby", "Six Nations"),
    ("cricket", "Indian Premier League"),
    ("esports", "League of Legends"),
    ("mma", "UFC 300"),
    ("boxing", "WBC World Title"),
    ("snooker", "World Snooker Championship"),
    ("horse_racing", "Cheltenham Festival"),
])
def test_new_sports_name_keywords_accepted(sport, name):
    c = scope.classify_competition(sport, name, "")
    assert c.allowed, f"expected accepted: {sport}/{name}"


@pytest.mark.parametrize("sport,name,slug", [
    ("ice_hockey", "AHL Regular Season", "ahl"),
    ("baseball", "Minor League Baseball", "milb"),
    ("american_football", "NCAA Football", "ncaa-football"),
    ("rugby", "Rugby Friendly", "rugby-friendly"),
    ("volleyball", "Local League", "local-league"),
    ("handball", "Regional League", "regional-league"),
    ("cricket", "Ranji Trophy", "ranji-trophy"),
    ("esports", "Amateur Cup", "amateur-cup"),
    ("darts", "Local Darts Night", "local-darts"),
    ("mma", "Regional Fight Night", "regional-mma"),
    ("boxing", "Amateur Boxing", "amateur-boxing"),
    ("snooker", "Qualifier Round", "qualifier"),
    ("horse_racing", "Novice Handicap", "novice-handicap"),
])
def test_new_sports_out_of_scope_rejected(sport, name, slug):
    c = scope.classify_competition(sport, name, slug)
    assert not c.allowed, f"expected rejected: {sport}/{name}"
    assert c.reason == "OUT_OF_SCOPE_COMPETITION"


def test_approved_sports_have_non_empty_whitelists():
    """No approved sport may be a blanket 'allow everything' entry."""
    for sport in scope.APPROVED_SPORTS:
        assert scope._COMPETITIONS[sport], f"{sport} has an empty slug whitelist"
        assert scope._NAME_KEYWORDS[sport], f"{sport} has no name-keyword fallback"


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


def test_league_tier_wrapper_multi_sport():
    assert _league_tier("NHL", "nhl") == 1
    assert _league_tier("Major League Baseball", "mlb") == 1
    assert _league_tier("Indian Premier League", "ipl") == 1
    assert _league_tier("UFC", "ufc") == 1


def test_league_tier_wrapper_out_of_scope_is_none():
    assert _league_tier("USA - MLS", "usa-mls") is None
    assert _league_tier("ITF - Tianjin", "itf-tianjin") is None


# ── Live odds-api.io provider slugs (verified against the /v3/leagues catalog
#    on 2026-09-25). Odds-API.io publishes leagues as <country>-<league>, so
#    these lock in the real slugs rather than the idealized ones. ─────────────

@pytest.mark.parametrize("sport,slug", [
    ("football", "spain-laliga"),
    ("basketball", "usa-nba"),
    ("basketball", "international-euroleague"),
    ("basketball", "spain-liga-acb"),
    ("basketball", "germany-bbl"),
    ("basketball", "france-pro-a"),
    ("basketball", "china-cba"),
    ("volleyball", "italy-superlega"),
    ("volleyball", "poland-liga-siatkowki"),
    ("volleyball", "germany-1st-bundesliga"),
    ("volleyball", "france-ligue-a"),
    ("handball", "germany-bundesliga"),
    ("handball", "france-lnh-starligue"),
    ("handball", "spain-asobal"),
    ("handball", "international-ehf-champions-league-group-a"),
    ("ice_hockey", "usa-nhl"),
    ("ice_hockey", "russia-khl"),
    ("ice_hockey", "finland-liiga"),
    ("ice_hockey", "sweden-shl"),
    ("ice_hockey", "czech-republic-extraliga"),
    ("rugby", "rugby-union-united-rugby-championship"),
    ("rugby", "rugby-union-english-premiership"),
    ("rugby", "rugby-league-nrl-premiership-playoffs"),
    ("baseball", "usa-mlb"),
    ("baseball", "republic-of-korea-kbo-league"),
    ("baseball", "chinese-taipei-cpbl"),
    ("american_football", "usa-nfl"),
    ("american_football", "canada-cfl"),
    ("cricket", "australia-big-bash-league"),
    ("boxing", "international-matchups"),
])
def test_live_provider_slugs_accepted(sport, slug):
    c = scope.classify_competition(sport, "", slug)
    assert c.allowed, f"expected accepted provider slug: {sport}/{slug}"
    assert c.tier and c.priority > 0


@pytest.mark.parametrize("sport,name", [
    ("darts", "International - World Grand Prix 2026"),
    ("snooker", "International - Shenzhen Open 2026"),
    ("table_tennis", "International - ETTU Champions League, Knockout Stage"),
    ("cricket", "International - ODI Series England vs. Sri Lanka"),
    ("baseball", "Japan - Professional Baseball, Central League"),
    ("basketball", "International - Euroleague"),
    ("volleyball", "Italy - SuperLega"),
    ("handball", "Germany - Bundesliga"),
])
def test_live_provider_names_accepted(sport, name):
    c = scope.classify_competition(sport, name, "")
    assert c.allowed, f"expected accepted provider name: {sport}/{name}"


@pytest.mark.parametrize("sport,name,slug", [
    ("basketball", "USA - NBA G League Tip-Off Tournament", "usa-nba-g-league-tip-off-tournament"),
    ("volleyball", "USA - NCAA, Women, Regular Season", "usa-ncaa-women-regular-season"),
    ("handball", "Hungary - NB I/B", "hungary-nb-i-b"),
    ("ice_hockey", "Germany - DEL 2", "germany-del-2"),
    ("baseball", "USA - MiLB, Triple-A Pacific Coast League", "usa-milb-triple-a-pacific-coast-league"),
    ("rugby", "Rugby Union - Pro D2", "rugby-union-pro-d2"),
    ("cricket", "South Africa - T20 South Africa Cup, Group 1", "south-africa-t20-south-africa-cup-group-1"),
    ("american_football", "USA - College", "usa-college"),
    ("football", "England - League One", "england-league-one"),
    ("football", "Germany - 3. Liga", "germany-3-liga"),
    ("boxing", "Bare Knuckle - BKFC 94", "bare-knuckle-bkfc-94"),
])
def test_live_provider_non_elite_rejected(sport, name, slug):
    c = scope.classify_competition(sport, name, slug)
    assert not c.allowed, f"expected rejected provider league: {sport}/{name}"
    assert c.reason == "OUT_OF_SCOPE_COMPETITION"
