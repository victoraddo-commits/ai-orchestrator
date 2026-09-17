"""KAI Bet — fixture verification clients (§21 SportyBet, §23 lineup/history).

SportyBet: confirm the EXACT fixture + market/odds before staking.
History: pull recent results/form to feed the screening engine.

SofaScore is attempted first but its API hard-403s (Cloudflare) from the runner;
the adapter falls back to free sources (TheSportsDB, openfootball).
Pure normalisation helpers are unit-tested; network calls are best-effort.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 15

SPORTYBET_API = "https://www.sportybet.com/api/ng/factsCenter"
THESPORTSDB_API = "https://www.thesportsdb.com/api/v1/json/3"


def _get(url: str, headers: Optional[Dict[str, str]] = None,
         timeout: int = _TIMEOUT) -> Optional[Dict[str, Any]]:
    hdrs = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ── SportyBet ─────────────────────────────────────────────────────────────────

def sportybet_event(event_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a fixture's markets/odds from SportyBet (verified exact market)."""
    url = f"{SPORTYBET_API}/event?productId=3&eventId={urllib.parse.quote(event_id)}"
    return _get(url, headers={"Referer": "https://www.sportybet.com/ng/sport/football"})


def sportybet_upcoming(tournament_id: str) -> Optional[Dict[str, Any]]:
    url = (f"{SPORTYBET_API}/pcUpcomingEvents?productId=3&sportId=sr%3Asport%3A1"
           f"&tournamentId={urllib.parse.quote(tournament_id)}&marketId=1&pageSize=100&pageNum=1")
    return _get(url, headers={"Referer": "https://www.sportybet.com/ng/sport/football"})


def normalise_markets(event_json: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten a SportyBet event payload into [{id,name,group,specifier,outcomes}]."""
    if not event_json:
        return []
    data = event_json.get("data", event_json)
    markets = []
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("markets"), list):
                markets.extend(o["markets"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    out = []
    for m in markets:
        outcomes = [{"desc": o.get("desc"), "odds": o.get("odds")}
                    for o in (m.get("outcomes") or [])]
        out.append({"id": str(m.get("id")), "name": m.get("desc") or m.get("name"),
                    "group": m.get("group"), "specifier": m.get("specifier", ""),
                    "guide": m.get("marketGuide", ""), "outcomes": outcomes})
    return out


def find_fixture(home: str, away: str, tournament_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Find a fixture on SportyBet by team names (across given/known tournaments)."""
    ids = tournament_ids or ["sr:tournament:35"]  # Bundesliga default; caller can widen
    h, a = home.strip().lower(), away.strip().lower()
    for tid in ids:
        up = sportybet_upcoming(tid)
        if not up:
            continue
        for t in (up.get("data", {}) or {}).get("tournaments", []):
            for ev in t.get("events", []):
                hn = (ev.get("homeTeamName") or "").lower()
                an = (ev.get("awayTeamName") or "").lower()
                if (h in hn or hn in h) and (a in an or an in a):
                    return {"eventId": ev.get("eventId"), "home": ev.get("homeTeamName"),
                            "away": ev.get("awayTeamName"),
                            "startTime": ev.get("estimateStartTime"),
                            "totalMarketSize": ev.get("totalMarketSize"),
                            "sportybet_markets": normalise_markets(ev)}
    return None


# ── Historical results / form ─────────────────────────────────────────────────

def thesportsdb_search_team(name: str) -> Optional[Dict[str, Any]]:
    d = _get(f"{THESPORTSDB_API}/searchteams.php?t={urllib.parse.quote(name)}")
    teams = (d or {}).get("teams") or []
    return teams[0] if teams else None


def thesportsdb_last_events(team_id: str) -> List[Dict[str, Any]]:
    d = _get(f"{THESPORTSDB_API}/eventslast.php?id={urllib.parse.quote(str(team_id))}")
    return (d or {}).get("results") or []


def results_to_form(events: List[Dict[str, Any]], team_id: str) -> List[str]:
    """Map TheSportsDB last events (most recent first) to W/D/L for a team."""
    out = []
    for ev in events:
        hs, as_ = ev.get("intHomeScore"), ev.get("intAwayScore")
        if hs is None or as_ is None:
            continue
        hs, as_ = int(hs), int(as_)
        home = str(ev.get("idHomeTeam")) == str(team_id)
        gf, ga = (hs, as_) if home else (as_, hs)
        out.append("W" if gf > ga else ("D" if gf == ga else "L"))
    return out


def goals_for_against(events: List[Dict[str, Any]], team_id: str) -> Dict[str, float]:
    gf = ga = n = 0
    for ev in events:
        hs, as_ = ev.get("intHomeScore"), ev.get("intAwayScore")
        if hs is None or as_ is None:
            continue
        hs, as_ = int(hs), int(as_)
        home = str(ev.get("idHomeTeam")) == str(team_id)
        gf += hs if home else as_
        ga += as_ if home else hs
        n += 1
    return {"games": n, "goals_for_pg": (gf / n) if n else 0.0,
            "goals_against_pg": (ga / n) if n else 0.0}


def team_history(team_name: str) -> Dict[str, Any]:
    """Best-effort team history: form + goals per game (TheSportsDB)."""
    t = thesportsdb_search_team(team_name)
    if not t:
        return {"team": team_name, "found": False}
    ev = thesportsdb_last_events(t["idTeam"])
    return {"team": t.get("strTeam"), "team_id": t.get("idTeam"), "found": True,
            "form": results_to_form(ev, t["idTeam"]),
            "rates": goals_for_against(ev, t["idTeam"]), "events": len(ev)}


def sofascore_search(query: str) -> Optional[Dict[str, Any]]:
    """Attempt SofaScore (usually 403 from the runner; kept for completeness)."""
    return _get(f"https://api.sofascore.com/api/v1/search/all?q={urllib.parse.quote(query)}",
                headers={"Referer": "https://www.sofascore.com/"})
