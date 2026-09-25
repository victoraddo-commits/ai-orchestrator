"""Kai Betting — approved sports & competition scope registry.

Single source of truth for what the system may process. Every event must
pass, in order:

    SPORT filter → COMPETITION filter → tier/priority sort → data quality
    → odds filter → prediction engine

Anything that fails the sport or competition filter is rejected BEFORE it
consumes API budget, AI inference, or a DB row.

The universe is deliberately narrow — each approved sport carries a non-empty
elite-competition whitelist, so "approved" never means "anything goes":

  - football         : top-5 domestic leagues × 2 divisions + major tournaments.
  - tennis           : Grand Slams, ATP/WTA Finals, Masters 1000 / WTA 1000, 500s.
  - basketball       : NBA + EuroLeague, major European/other pro leagues, FIBA.
  - ice_hockey       : NHL/KHL + top European leagues + CHL/world championship.
  - baseball         : MLB + NPB/KBO/CPBL + World Series / WBC.
  - american_football: NFL + CFL + XFL/USFL + Super Bowl.
  - rugby            : Six Nations, Rugby Championship, URC, Top 14, World Cup.
  - volleyball       : CEV Champions League + top domestic leagues + Nations League.
  - handball         : EHF Champions League + top domestic leagues + EHF Euro.
  - cricket          : IPL, Big Bash, The Hundred, PSL, CPL + ICC world events.
  - esports          : LoL, CS2, Dota 2, Valorant majors + premier regional leagues.
  - darts            : PDC World Championship + Premier League/Matchplay/Grand Prix.
  - mma              : UFC, Bellator, PFL, ONE Championship.
  - boxing           : WBC/WBA/IBF/WBO world-title cards + major promoters.
  - table_tennis     : WTT / ITTF world events.
  - snooker          : World Championship, UK Championship, Masters, British Open.
  - horse_racing     : the classic elite meetings (Cheltenham, Royal Ascot,
                       Grand National, Kentucky Derby, Melbourne Cup, …) —
                       APPROVED but with NO configured odds provider today;
                       ingestion reports ``no_source`` rather than fabricating
                       a feed (see data_sources/horse_racing.py).

Competitions are whitelisted by NORMALIZED SLUG (underscore→hyphen, lowercased)
— the one provider field that is stable and unique. Odds-API.io publishes most
leagues as ``<country>-<league>`` (``usa-nba``, ``finland-liiga``,
``spain-laliga``, ``rugby-union-united-rugby-championship``), so the whitelists
carry those exact provider slugs, with a name-keyword fallback for the
continental/international tournaments whose slugs vary by season
(e.g. ``international-world-grand-prix-2026``).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# ── Approved sports ───────────────────────────────────────────────────────────
APPROVED_SPORTS = frozenset({
    "football",
    "tennis",
    "basketball",
    "ice_hockey",
    "baseball",
    "american_football",
    "rugby",
    "volleyball",
    "handball",
    "cricket",
    "esports",
    "darts",
    "mma",
    "boxing",
    "table_tennis",
    "snooker",
    "horse_racing",
})

# Sports with a live odds provider wired in. horse_racing is approved at the
# scope layer but has no feed yet, so it must never be advertised as syncing.
SPORTS_WITH_SOURCE = frozenset(APPROVED_SPORTS - {"horse_racing"})

# Default per-sport sync order (source-backed sports only). Kept as a list so
# betting_config's comma-separated default and data_ingestion fallback agree.
DEFAULT_SYNC_SPORTS = (
    "football", "basketball", "tennis",
    "ice_hockey", "baseball", "american_football",
    "rugby", "volleyball", "handball", "cricket",
    "esports", "darts", "mma", "boxing", "table_tennis", "snooker",
)

# ── Tier codes ────────────────────────────────────────────────────────────────
# Football: S = top division, A = second division, T = tournament.
# Other sports: 1..4 = competition class, T = tournament.
_TIER_TO_DB = {"S": 1, "A": 2, "T": 3, "1": 1, "2": 2, "3": 3, "4": 3}
_DB_EXCLUDED = 99


def db_tier(tier_code: str) -> int:
    """Numeric tier for the leagues.tier column (99 = excluded)."""
    return _TIER_TO_DB.get(tier_code, _DB_EXCLUDED)


# ── Competition whitelists (normalized slug → (tier, priority)) ───────────────
_FOOTBALL: dict = {
    # England
    "epl": ("S", 100), "england-premier-league": ("S", 100), "premier-league": ("S", 100),
    "efl-championship": ("A", 80), "championship": ("A", 80),
    "england-championship": ("A", 80), "efl-champ": ("A", 80),
    # Spain
    "spain-la-liga": ("S", 100), "la-liga": ("S", 100), "laliga": ("S", 100),
    "spain-laliga": ("S", 100),  # live odds-api.io slug
    "spain-segunda-division": ("A", 80), "segunda-division": ("A", 80),
    "laliga-2": ("A", 80), "la-liga-2": ("A", 80),
    "spain-laliga-2": ("A", 80),  # live odds-api.io slug
    # Italy
    "italy-serie-a": ("S", 100), "serie-a": ("S", 100),
    "italy-serie-b": ("A", 80), "serie-b": ("A", 80),
    # Germany
    "germany-bundesliga": ("S", 100), "bundesliga": ("S", 100),
    "germany-2-bundesliga": ("A", 80), "2-bundesliga": ("A", 80), "bundesliga-2": ("A", 80),
    # France
    "france-ligue-1": ("S", 100), "france-ligue-one": ("S", 100), "ligue-1": ("S", 100),
    "france-ligue-2": ("A", 80), "france-ligue-two": ("A", 80), "ligue-2": ("A", 80),
}

_TENNIS: dict = {
    "australian-open": ("1", 100),
    "roland-garros": ("1", 100), "french-open": ("1", 100),
    "wimbledon": ("1", 100),
    "us-open": ("1", 100),
    "atp-finals": ("2", 90), "wta-finals": ("2", 90),
    "atp-masters-1000": ("3", 80), "masters-1000": ("3", 80),
    "wta-1000": ("3", 80),
    "atp-500": ("4", 70), "wta-500": ("4", 70),
}

_BASKETBALL: dict = {
    "nba": ("1", 100),
    "euroleague": ("1", 100),
    "acb": ("2", 80), "liga-acb": ("2", 80), "liga-endesa": ("2", 80),
    "lega-basket-serie-a": ("2", 80), "lega-basket": ("2", 80),
    "basketball-bundesliga": ("2", 80), "bbl": ("2", 80),
    "lnb-pro-a": ("2", 80), "lnb": ("2", 80),
    "turkish-basketball-super-league": ("2", 80), "bsl": ("2", 80),
    "greek-basket-league": ("2", 80), "greek-basket": ("2", 80), "a1-ethniki": ("2", 80),
    "wnba": ("2", 80),
    "nbl": ("2", 80),
    "cba": ("2", 80),
    # Live odds-api.io slugs (country-prefixed as the provider publishes them)
    "usa-nba": ("1", 100), "usa-nba-preseason": ("3", 60),
    "international-euroleague": ("1", 100),
    "international-eurocup-group-a": ("2", 80), "international-eurocup-group-b": ("2", 80),
    "international-eurocup-group-c": ("2", 80), "international-eurocup-group-d": ("2", 80),
    "international-champions-league-group-a": ("2", 80), "international-champions-league-group-b": ("2", 80),
    "international-champions-league-group-c": ("2", 80), "international-champions-league-group-d": ("2", 80),
    "international-champions-league-group-e": ("2", 80), "international-champions-league-group-f": ("2", 80),
    "international-champions-league-group-g": ("2", 80), "international-champions-league-group-h": ("2", 80),
    "international-fiba-champions-league-group-c": ("2", 80), "international-fiba-champions-league-group-e": ("2", 80),
    "international-fiba-champions-league-group-f": ("2", 80), "international-fiba-champions-league-group-g": ("2", 80),
    "international-fiba-champions-league-group-h": ("2", 80),
    "spain-liga-acb": ("2", 80), "italy-serie-a": ("2", 80),
    "germany-bbl": ("2", 80), "germany-bbl-pokal": ("3", 60),
    "france-pro-a": ("2", 80), "greece-greek-basketball-league": ("2", 80),
    "turkiye-super-lig": ("2", 80), "china-cba": ("2", 80),
    "australia-nbl": ("2", 80), "japan-b1-league": ("2", 80),
    "republic-of-korea-kbl": ("2", 80), "israel-super-league": ("2", 80),
    "poland-basket-liga": ("2", 80), "russia-super-league": ("2", 80),
    "international-aba-liga-group-a": ("2", 80), "international-aba-liga-group-b": ("2", 80),
    "philippines-pba-governors-cup-group-a": ("2", 80), "philippines-pba-governors-cup-group-b": ("2", 80),
}

_ICE_HOCKEY: dict = {
    "nhl": ("1", 100),
    "khl": ("1", 90),
    "shl": ("2", 80), "swedish-hockey-league": ("2", 80),
    "liiga": ("2", 80), "sm-liiga": ("2", 80),
    "del": ("2", 80), "germany-del": ("2", 80),
    "national-league": ("2", 80), "swiss-national-league": ("2", 80),
    "czech-extraliga": ("2", 80), "extraliga": ("2", 80),
    "champions-hockey-league": ("T", 60), "chl": ("T", 60),
    "world-championship": ("T", 60),
    "olympic-hockey": ("T", 60),
    # Live odds-api.io slugs (country-prefixed)
    "usa-nhl": ("1", 100), "usa-nhl-preseason": ("3", 60),
    "russia-khl": ("1", 90),
    "sweden-shl": ("2", 80), "finland-liiga": ("2", 80), "germany-del": ("2", 80),
    "switzerland-national-league": ("2", 80), "czech-republic-extraliga": ("2", 80),
    "slovakia-extraliga": ("2", 80), "austria-ice-hockey-league": ("2", 80),
    "denmark-superisligaen": ("2", 80), "norway-ehl": ("2", 80),
    "france-ligue-magnus": ("2", 80), "england-elite-league": ("2", 80),
    "italy-ihl": ("2", 70), "poland-ekstraliga": ("2", 70),
    "belarus-extraliga": ("2", 70), "kazakhstan-pro-hockey-league": ("2", 70),
    "latvia-latvian-hockey-league": ("2", 70), "usa-ahl": ("2", 70),
    "international-champions-hockey-league": ("T", 60),
    "international-deutschland-cup": ("T", 60),
}

_BASEBALL: dict = {
    "mlb": ("1", 100),
    "npb": ("2", 80), "nippon-professional-baseball": ("2", 80),
    "kbo": ("2", 80),
    "cpbl": ("2", 80),
    "world-baseball-classic": ("T", 60),
    "world-series": ("T", 60),
    # Live odds-api.io slugs (country-prefixed)
    "usa-mlb": ("1", 100),
    "japan-professional-baseball-central-league": ("2", 80),
    "japan-professional-baseball-pacific-league": ("2", 80),
    "republic-of-korea-kbo-league": ("2", 80), "chinese-taipei-cpbl": ("2", 80),
    "mexico-liga-mexicana-del-pacifico": ("2", 70),
    "dominican-republic-lidom": ("2", 70), "australia-abl": ("2", 70),
    "international-asia-professional-championship": ("T", 60),
}

_AMERICAN_FOOTBALL: dict = {
    "nfl": ("1", 100),
    "cfl": ("2", 80),
    "xfl": ("2", 70), "usfl": ("2", 70),
    "super-bowl": ("T", 60),
    # Live odds-api.io slugs
    "usa-nfl": ("1", 100), "canada-cfl": ("2", 80),
}

_RUGBY: dict = {
    "six-nations": ("1", 100),
    "rugby-championship": ("1", 100),
    "united-rugby-championship": ("2", 80), "urc": ("2", 80),
    "premiership-rugby": ("2", 80), "gallagher-premiership": ("2", 80),
    "top-14": ("2", 80),
    "super-rugby": ("2", 80),
    "rugby-world-cup": ("T", 60),
    "champions-cup": ("T", 60), "heineken-champions-cup": ("T", 60),
    # Live odds-api.io slugs
    "rugby-union-united-rugby-championship": ("2", 80),
    "rugby-union-english-premiership": ("2", 80),
    "rugby-union-france-top-14": ("2", 80),
    "rugby-union-super-rugby-aus": ("2", 80),
    "rugby-union-rfu-championship": ("2", 70),
    "rugby-union-superliga-playoffs": ("2", 70),
    "rugby-union-european-rugby-champions-cup-pool-a": ("T", 60),
    "rugby-union-european-rugby-champions-cup-pool-b": ("T", 60),
    "rugby-union-european-rugby-champions-cup-pool-3": ("T", 60),
    "rugby-union-european-rugby-challenge-cup-pool-a": ("T", 60),
    "rugby-union-european-rugby-challenge-cup-pool-b": ("T", 60),
    "rugby-union-european-rugby-challenge-cup-pool-3": ("T", 60),
    "rugby-union-nations-championship-group-stage": ("T", 60),
    "rugby-league-nrl-premiership-playoffs": ("2", 80),
    "rugby-league-super-league-playoffs": ("2", 70),
    "rugby-league-world-cup-group-a": ("T", 60),
    "rugby-league-world-cup-group-b-and-c": ("T", 60),
}

_VOLLEYBALL: dict = {
    "cev-champions-league": ("1", 100), "champions-league-volleyball": ("1", 100),
    "superlega": ("2", 80), "italian-superlega": ("2", 80),
    "plusliga": ("2", 80),
    "volleyball-nations-league": ("T", 60), "nations-league": ("T", 60),
    "world-championship": ("T", 60),
    # Live odds-api.io slugs
    "italy-superlega": ("1", 100),
    "poland-liga-siatkowki": ("2", 80), "germany-1st-bundesliga": ("2", 80),
    "france-ligue-a": ("2", 80), "russia-superliga": ("2", 80),
    "japan-sv-league": ("2", 80),
    "netherlands-eredivisie": ("2", 70), "belgium-1st-league": ("2", 70),
    "greece-volley-league": ("2", 70), "spain-superliga": ("2", 70),
    "czechia-extraliga": ("2", 70), "finland-mestaruusliiga": ("2", 70),
    "sweden-elitserien": ("2", 70), "switzerland-nla": ("2", 70),
    "austria-volley-league": ("2", 70), "denmark-volleyligaen": ("2", 70),
    "bulgaria-super-league": ("2", 70), "croatia-superliga": ("2", 70),
    "hungary-nb-i-extraliga": ("2", 70), "portugal-liga-a1-main-round": ("2", 70),
    "romania-div-1": ("2", 70), "israel-premier-league": ("2", 70),
    "iran-super-league-group-a": ("2", 70), "iran-super-league-group-b": ("2", 70),
    "international-cev-cup": ("T", 60),
    "international-cev-eurovolley-playoffs": ("T", 60),
    "international-challenge-cup": ("T", 60),
}

_HANDBALL: dict = {
    "ehf-champions-league": ("1", 100), "champions-league-handball": ("1", 100),
    "handball-bundesliga": ("2", 80), "hbl": ("2", 80),
    "lnh": ("2", 80), "france-lnh": ("2", 80),
    "liga-asobal": ("2", 80), "asobal": ("2", 80),
    "world-championship": ("T", 60), "ehf-euro": ("T", 60),
    # Live odds-api.io slugs
    "germany-bundesliga": ("2", 80), "france-lnh-starligue": ("2", 80),
    "spain-asobal": ("2", 80), "denmark-handboldligaen": ("2", 80),
    "sweden-handbollsligan": ("2", 80), "norway-eliteserien": ("2", 80),
    "poland-superliga": ("2", 80), "hungary-nb-i": ("2", 80),
    "croatia-premijer-liga-liga-a": ("2", 80),
    "portugal-1a-divisao": ("2", 70), "romania-liga-nationala": ("2", 70),
    "serbia-super-liga": ("2", 70), "slovakia-extraliga": ("2", 70),
    "czech-republic-extraliga": ("2", 70), "austria-hla-meisterliga": ("2", 70),
    "switzerland-mnla": ("2", 70), "north-macedonia-super-liga": ("2", 70),
    "turkiye-superlig": ("2", 70), "russia-super-league": ("2", 70),
    "greece-premier-1": ("2", 70), "iceland-urvalsdeild": ("2", 70),
    "international-ehf-champions-league-group-a": ("1", 100),
    "international-ehf-champions-league-group-c": ("1", 100),
    "international-ehf-champions-league-group-d": ("1", 100),
    "international-ehf-champions-league-group-e": ("1", 100),
    "international-ehf-champions-league-group-f": ("1", 100),
    "international-ehf-european-league-group-a": ("T", 60),
    "international-ehf-european-league-group-b": ("T", 60),
    "international-ehf-european-league-group-c": ("T", 60),
    "international-ehf-european-league-group-d": ("T", 60),
    "international-ehf-european-league-group-e": ("T", 60),
    "international-ehf-european-league-group-f": ("T", 60),
    "international-ehf-european-league-group-g": ("T", 60),
    "international-ehf-european-league-group-h": ("T", 60),
    "international-ehf-eurocup": ("T", 60),
    "international-ihf-club-world-championship-group-a": ("T", 60),
    "international-ihf-club-world-championship-group-b": ("T", 60),
    "international-mol-liga": ("T", 60),
    "international-super-handball-league": ("T", 60),
    "international-european-cup": ("T", 60),
}

_CRICKET: dict = {
    "ipl": ("1", 100), "indian-premier-league": ("1", 100),
    "big-bash-league": ("2", 80), "bbl-cricket": ("2", 80),
    "the-hundred": ("2", 80),
    "pakistan-super-league": ("2", 80), "psl": ("2", 80),
    "caribbean-premier-league": ("2", 80), "cpl": ("2", 80),
    "t20-world-cup": ("T", 60), "icc-t20-world-cup": ("T", 60),
    "icc-cricket-world-cup": ("T", 60), "cricket-world-cup": ("T", 60),
    "world-test-championship": ("T", 60), "the-ashes": ("T", 60),
    # Live odds-api.io slugs
    "australia-big-bash-league": ("2", 80),
    "international-t20-north-american-cup": ("2", 70),
}

_ESPORTS: dict = {
    "league-of-legends": ("1", 100), "lol": ("1", 100),
    "cs2": ("1", 100), "csgo": ("1", 100), "counter-strike": ("1", 100),
    "dota-2": ("1", 100), "dota2": ("1", 100),
    "valorant": ("1", 100),
    "overwatch": ("2", 80), "overwatch-league": ("2", 80),
    "lcs": ("2", 80), "lec": ("2", 80), "lck": ("2", 80), "lpl": ("2", 80),
    "pgl-major": ("T", 60), "the-international": ("T", 60),
}

_DARTS: dict = {
    "pdc-world-championship": ("1", 100), "world-darts-championship": ("1", 100),
    "premier-league-darts": ("2", 80),
    "world-matchplay": ("2", 80),
    "world-grand-prix": ("2", 80),
    "grand-slam-of-darts": ("2", 80),
    "uk-open": ("2", 80),
}

_MMA: dict = {
    "ufc": ("1", 100),
    "bellator": ("2", 80), "bellator-mma": ("2", 80),
    "pfl": ("2", 80), "professional-fighters-league": ("2", 80),
    "one-championship": ("2", 80), "one-fc": ("2", 80),
}

_BOXING: dict = {
    "wbc": ("1", 100), "wba": ("1", 100), "ibf": ("1", 100), "wbo": ("1", 100),
    "matchroom": ("2", 80), "top-rank": ("2", 80),
    "golden-boy": ("2", 80), "queensberry": ("2", 80),
    "world-title": ("T", 60), "heavyweight": ("T", 60),
    # Live odds-api.io slugs: provider labels boxing cards generically (no
    # sanctioning-body tags, and "Bare Knuckle" is deliberately excluded).
    "boxing-matches": ("2", 70), "international-matchups": ("2", 70),
}

_TABLE_TENNIS: dict = {
    "wtt": ("1", 100), "world-table-tennis": ("1", 100),
    "ittf-world-championships": ("T", 60), "world-championship": ("T", 60),
    "champions-league-table-tennis": ("2", 80),
}

_SNOOKER: dict = {
    "world-snooker-championship": ("1", 100),
    "uk-championship": ("2", 80),
    "masters": ("2", 80), "snooker-masters": ("2", 80),
    "champion-of-champions": ("2", 80),
    "british-open": ("2", 80),
}

_HORSE_RACING: dict = {
    "cheltenham": ("1", 100), "cheltenham-festival": ("1", 100),
    "royal-ascot": ("1", 100),
    "grand-national": ("1", 100), "aintree-grand-national": ("1", 100),
    "kentucky-derby": ("1", 100),
    "melbourne-cup": ("1", 100),
    "breeders-cup": ("1", 100),
    "prix-de-larc-de-triomphe": ("1", 100),
    "king-george": ("1", 100), "king-george-vi": ("1", 100),
    "dubai-world-cup": ("1", 100),
}

# Name-keyword fallbacks, uniform shape: (keyword, tier, priority). Used only
# where the slug is unreliable (tournaments, Grand Slams). Matched as a
# substring of the lowercased league name.
_FOOTBALL_NAMES: tuple = (
    ("uefa champions league", "T", 60),
    ("uefa europa league", "T", 60),
    ("uefa conference league", "T", 60),
    ("club world cup", "T", 60),
    ("fifa world cup", "T", 60),
    ("european championship", "T", 60),
    ("copa america", "T", 60),
    ("africa cup", "T", 60), ("afcon", "T", 60),
    ("uefa nations league", "T", 60),
)

_TENNIS_NAMES: tuple = (
    ("australian open", "1", 100),
    ("roland garros", "1", 100), ("french open", "1", 100),
    ("wimbledon", "1", 100),
    ("us open", "1", 100),
    ("atp finals", "2", 90), ("wta finals", "2", 90),
    ("masters 1000", "3", 80), ("atp masters", "3", 80),
    ("wta 1000", "3", 80),
    ("atp 500", "4", 70), ("wta 500", "4", 70),
    # The provider names ATP/WTA tour events by city ("ATP - Cincinnati, USA")
    # rather than by tier, so a bare "atp"/"wta" catch-all admits the elite tour
    # while the reject-list still excludes ITF / Challenger / Futures / juniors.
    ("atp", "4", 70), ("wta", "4", 70),
)

_BASKETBALL_NAMES: tuple = (
    ("fiba world cup", "T", 60), ("fiba eurobasket", "T", 60),
    ("olympic basketball", "T", 60),
    ("euroleague", "1", 100), ("eurocup", "2", 80),
    ("liga acb", "2", 80), ("nba", "1", 100),
    ("greek basketball league", "2", 80),
    ("fiba champions league", "2", 80), ("basketball champions league", "2", 80),
)

_ICE_HOCKEY_NAMES: tuple = (
    ("nhl", "1", 100), ("khl", "1", 90),
    ("stanley cup", "T", 60),
    ("ice hockey world championship", "T", 60),
    ("champions hockey league", "T", 60),
    ("ice hockey league", "2", 80), ("ligue magnus", "2", 80),
    ("deutschland cup", "T", 60),
)

_BASEBALL_NAMES: tuple = (
    ("mlb", "1", 100),
    ("world series", "T", 60),
    ("world baseball classic", "T", 60),
    ("kbo", "2", 80), ("cpbl", "2", 80),
    ("central league", "2", 80), ("pacific league", "2", 80),
)

_AMERICAN_FOOTBALL_NAMES: tuple = (
    ("nfl", "1", 100),
    ("super bowl", "T", 60),
    ("cfl", "2", 80),
)

_RUGBY_NAMES: tuple = (
    ("six nations", "1", 100),
    ("rugby world cup", "T", 60),
    ("super rugby", "2", 80),
    ("united rugby championship", "2", 80),
    ("english premiership", "2", 80), ("nations championship", "T", 60),
    ("challenge cup", "T", 60), ("nrl", "2", 80),
    ("rugby league - world cup", "T", 60),
)

_VOLLEYBALL_NAMES: tuple = (
    ("cev champions league", "1", 100),
    ("volleyball nations league", "T", 60),
    ("volleyball world championship", "T", 60),
    ("superlega", "1", 100), ("liga siatkowki", "2", 80),
    ("cev cup", "T", 60), ("cev eurovolley", "T", 60),
    ("volley league", "2", 70), ("sv league", "2", 80),
    ("mestaruusliiga", "2", 70),
)

_HANDBALL_NAMES: tuple = (
    ("ehf champions league", "1", 100),
    ("handball world championship", "T", 60),
    ("ehf euro", "T", 60),
    ("starligue", "2", 80), ("asobal", "2", 80),
    ("germany - bundesliga", "2", 80),
    ("handboldligaen", "2", 80), ("handbollsligan", "2", 80),
    ("ehf european league", "T", 60), ("ihf club world championship", "T", 60),
    ("mol liga", "T", 60), ("hla meisterliga", "2", 70),
)

_CRICKET_NAMES: tuple = (
    ("indian premier league", "1", 100), ("ipl", "1", 100),
    ("t20 world cup", "T", 60),
    ("cricket world cup", "T", 60),
    ("the ashes", "T", 60),
    ("icc cricket world cup", "T", 60), ("big bash", "2", 80),
    ("odi series", "T", 60), ("t20 series", "T", 60), ("test series", "T", 60),
)

_ESPORTS_NAMES: tuple = (
    ("league of legends", "1", 100),
    ("counter-strike", "1", 100),
    ("dota", "1", 100),
    ("valorant", "1", 100),
    ("the international", "T", 60),
)

_DARTS_NAMES: tuple = (
    ("pdc world championship", "1", 100),
    ("premier league darts", "2", 80),
    ("world matchplay", "2", 80),
    ("world grand prix", "2", 80),
)

_MMA_NAMES: tuple = (
    ("ufc", "1", 100),
    ("bellator", "2", 80),
    ("one championship", "2", 80),
    ("pfl", "2", 80),
)

_BOXING_NAMES: tuple = (
    ("wbc", "1", 100), ("wba", "1", 100),
    ("ibf", "1", 100), ("wbo", "1", 100),
    ("heavyweight title", "T", 60),
)

_TABLE_TENNIS_NAMES: tuple = (
    ("world table tennis", "1", 100), ("wtt", "1", 100),
    ("ittf world championship", "T", 60),
    ("ettu champions league", "2", 80),
)

_SNOOKER_NAMES: tuple = (
    ("world snooker championship", "1", 100),
    ("uk championship", "2", 80),
    ("snooker", "2", 80),
    ("northern ireland open", "2", 80), ("shenzhen open", "2", 80),
    ("scottish open", "2", 80), ("english open", "2", 80), ("welsh open", "2", 80),
)

_HORSE_RACING_NAMES: tuple = (
    ("cheltenham", "1", 100),
    ("royal ascot", "1", 100),
    ("grand national", "1", 100),
    ("kentucky derby", "1", 100),
    ("melbourne cup", "1", 100),
    ("breeders' cup", "1", 100), ("breeders cup", "1", 100),
)

_COMPETITIONS = {
    "football": _FOOTBALL,
    "tennis": _TENNIS,
    "basketball": _BASKETBALL,
    "ice_hockey": _ICE_HOCKEY,
    "baseball": _BASEBALL,
    "american_football": _AMERICAN_FOOTBALL,
    "rugby": _RUGBY,
    "volleyball": _VOLLEYBALL,
    "handball": _HANDBALL,
    "cricket": _CRICKET,
    "esports": _ESPORTS,
    "darts": _DARTS,
    "mma": _MMA,
    "boxing": _BOXING,
    "table_tennis": _TABLE_TENNIS,
    "snooker": _SNOOKER,
    "horse_racing": _HORSE_RACING,
}
_NAME_KEYWORDS = {
    "football": _FOOTBALL_NAMES,
    "tennis": _TENNIS_NAMES,
    "basketball": _BASKETBALL_NAMES,
    "ice_hockey": _ICE_HOCKEY_NAMES,
    "baseball": _BASEBALL_NAMES,
    "american_football": _AMERICAN_FOOTBALL_NAMES,
    "rugby": _RUGBY_NAMES,
    "volleyball": _VOLLEYBALL_NAMES,
    "handball": _HANDBALL_NAMES,
    "cricket": _CRICKET_NAMES,
    "esports": _ESPORTS_NAMES,
    "darts": _DARTS_NAMES,
    "mma": _MMA_NAMES,
    "boxing": _BOXING_NAMES,
    "table_tennis": _TABLE_TENNIS_NAMES,
    "snooker": _SNOOKER_NAMES,
    "horse_racing": _HORSE_RACING_NAMES,
}

# Name keywords sorted longest-first so a specific match wins.
_NAME_KEYWORDS_SORTED = {
    sport: tuple(sorted(kws, key=lambda kt: -len(kt[0])))
    for sport, kws in _NAME_KEYWORDS.items()
}


def _normalize_slug(slug: str) -> str:
    return (slug or "").replace("_", "-").strip().lower()


def _normalize_name(name: str) -> str:
    """Lowercase + strip accents so 'Copa América' matches 'copa america'."""
    normalized = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


# Reject-list substrings checked on the name BEFORE the accept whitelist, so a
# junior/youth/amateur/ITF/friendly event that happens to contain an approved
# keyword (e.g. "Junior - Wimbledon") is still rejected.
_REJECT_NAME_KEYWORDS: tuple = (
    "junior", "youth", "amateur", "exhibition", "friendly", "reserve",
    "u-17", "u-19", "u-20", "u-21", "u-23",
    "u17", "u19", "u20", "u21", "u23",
    "college", "ncaa", "itf", "futures", "challenger", "g league",
    "qualif", "playoff", "play-off", "preliminary", "prelim",
    "125k",  # WTA 125K sits below the approved WTA 500 tier
)


@dataclass(frozen=True)
class Classification:
    allowed: bool
    sport: str
    tier: str      # 'S'|'A'|'T' (football), '1'..'4'|'T' (other); '' if not allowed
    priority: int  # 0 if not allowed
    reason: str    # '' if allowed; else a stable reason code


def sport_allowed(sport_key: str) -> bool:
    return sport_key in APPROVED_SPORTS


def sport_has_source(sport_key: str) -> bool:
    """True when an odds provider is configured for the sport today."""
    return sport_key in SPORTS_WITH_SOURCE


def classify_competition(
    sport_key: str,
    league_name: str = "",
    league_slug: str = "",
) -> Classification:
    """Classify a league/competition for a sport.

    Returns a Classification; ``allowed`` is False for out-of-scope sports or
    competitions, with a stable ``reason`` the pipeline can log/act on.
    """
    if sport_key not in APPROVED_SPORTS:
        return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_SPORT")

    slug = _normalize_slug(league_slug)
    entry = _COMPETITIONS[sport_key].get(slug)
    if entry is not None:
        tier, priority = entry
        return Classification(True, sport_key, tier, priority, "")

    name = _normalize_name(league_name)
    if any(k in name for k in _REJECT_NAME_KEYWORDS):
        return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_COMPETITION")

    for keyword, tier, priority in _NAME_KEYWORDS_SORTED[sport_key]:
        if keyword in name:
            return Classification(True, sport_key, tier, priority, "")

    return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_COMPETITION")
