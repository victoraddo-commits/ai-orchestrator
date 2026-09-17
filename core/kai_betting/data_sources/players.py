"""KAI Bet — player/squad ingestion for prop markets (§6.2/§23).

What is reachable free: **squads** (TheSportsDB). What is NOT reachable free:
per-match player stats (shots/assists/on-target) — SofaScore hard-403s and
TheSportsDB's lineup/stats endpoints are premium.

So this module ingests squads now, and exposes `player_stats_available()`
which is False until a stats source is wired — prop markets stay `modelled=False`
(honest, §44: never fabricate). Pure parsers are unit-tested.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

_UA = "Mozilla/5.0 (X11; Linux x86_64) kai-betting/1.0"
_BASE = "https://www.thesportsdb.com/api/v1/json/3"


def _get(url: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def parse_squad(doc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise TheSportsDB lookup_all_players into [{id,name,position,nationality,number}]."""
    out = []
    for p in (doc or {}).get("player") or []:
        out.append({
            "id": str(p.get("idPlayer") or ""),
            "name": p.get("strPlayer") or "",
            "position": (p.get("strPosition") or "").strip(),
            "nationality": p.get("strNationality") or "",
            "number": p.get("strNumber") or "",
        })
    return [p for p in out if p["name"]]


def squad_by_team_id(team_id: str) -> List[Dict[str, Any]]:
    doc = _get(f"{_BASE}/lookup_all_players.php?id={urllib.parse.quote(str(team_id))}")
    return parse_squad(doc)


def squad_by_team_name(team_name: str) -> List[Dict[str, Any]]:
    t = _get(f"{_BASE}/searchteams.php?t={urllib.parse.quote(team_name)}")
    teams = (t or {}).get("teams") or []
    if not teams:
        return []
    return squad_by_team_id(teams[0].get("idTeam"))


def player_stats_available() -> bool:
    """Per-match player stats (shots/assists/on-target) are not available free.

    Flip to True only when a real stats source is wired; prop families depend
    on this and stay unmodelled until then.
    """
    return False


def stats_source_note() -> str:
    if player_stats_available():
        return "player stats available"
    return ("per-match player stats unavailable (SofaScore 403; TheSportsDB stats "
            "premium) — prop markets remain unmodelled")


def ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS players (
        id TEXT PRIMARY KEY, team TEXT, name TEXT, position TEXT,
        nationality TEXT, number TEXT, updated_at TEXT)""")


def store_squad(conn, team: str, players: List[Dict[str, Any]]) -> int:
    """Upsert a team's squad into the `players` table. Returns rows written."""
    import time
    ensure_table(conn)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for p in players:
        if not p.get("id"):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO players (id,team,name,position,nationality,number,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (p["id"], team, p["name"], p.get("position", ""),
             p.get("nationality", ""), p.get("number", ""), now))
        n += 1
    conn.commit()
    return n
