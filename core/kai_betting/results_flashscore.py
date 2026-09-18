"""FlashScore results backfill.

The runner (CT111) can reach the FlashScore ninja feed, but SofaScore and
SportyBet are Cloudflare-403 and TheSportsDB's free tier lacks these fixtures.
This module fetches finished fixtures from the FlashScore feed and writes final
scores onto matching `events`, so `auto_settle_finished()` can grade predictions.

Matching: football by token-set similarity; tennis by surname. Adjacent days are
scanned to absorb timezone drift.
"""
from __future__ import annotations

import logging
import re
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from core.kai_betting.db import get_db

logger = logging.getLogger(__name__)

_HDRS = {"User-Agent": "Mozilla/5.0", "x-fsign": "SW9D1eZo"}
_BASE = "https://local-global.flashscore.ninja/2/x/feed"
_SPORTS = (1, 2, 3, 4)   # football, tennis, basketball, hockey
_STOP = {"fc", "cf", "afc", "sc", "ac", "cd", "club", "de", "the", "sv", "sk", "fk",
         "if", "bk", "u19", "u20", "u21", "u23", "ii", "b", "w", "women", "youth",
         "reserves", "srl", "sa", "as", "us", "ss", "cs", "kf", "nk", "hnk", "gfc"}


def _tokens(name: str) -> set:
    s = re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\[[^\]]*\]", " ", (name or "").lower()))
    return {t for t in s.split() if t and t not in _STOP}


def _surname(name: str) -> str:
    s = (name or "").lower()
    if "," in s:
        s = s.split(",", 1)[0]
    parts = s.split()
    return re.sub(r"[^a-z]", "", parts[0]) if parts else ""


def _sim(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _fetch_day(offset: int, sport: int) -> list:
    try:
        req = urllib.request.Request(f"{_BASE}/f_{sport}_{offset}_3_en_1", headers=_HDRS)
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for block in raw.split("~"):
        if "AA÷" not in block:
            continue
        f = {}
        for tok in block.split("¬"):
            if "÷" in tok:
                k, v = tok.split("÷", 1)
                f.setdefault(k, v)
        if f.get("AG") and f.get("AH") and f.get("AE") and f.get("AF"):
            try:
                out.append({"home": f["AE"], "away": f["AF"],
                            "hs": int(f["AG"]), "as": int(f["AH"])})
            except ValueError:
                pass
    return out


def backfill(days_back: int = 4, threshold: float = 0.5) -> dict:
    """Score unscored events from FlashScore and return a summary."""
    today = datetime.now(timezone.utc).date()
    with get_db() as db:
        rows = db.execute("""
            SELECT e.id, e.event_time, s.key AS sport_key, ht.name AS home, at.name AS away
            FROM events e
            LEFT JOIN teams ht ON ht.id = e.home_team_id
            LEFT JOIN teams at ON at.id = e.away_team_id
            LEFT JOIN sports s ON s.id = e.sport_id
            WHERE (e.home_score IS NULL OR e.away_score IS NULL)
              AND e.status NOT IN ('cancelled', 'postponed') AND e.event_time IS NOT NULL
        """).fetchall()

    by_date: dict = {}
    for r in rows:
        try:
            d = datetime.fromisoformat((r["event_time"] or "").replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d <= today:
            by_date.setdefault(d, []).append(r)

    cache: dict = {}

    def pool(offset: int) -> list:
        if offset not in cache:
            p: list = []
            for sp in _SPORTS:
                p += _fetch_day(offset, sp)
            cache[offset] = p
        return cache[offset]

    scored = 0
    for d, evs in by_date.items():
        base = (d - today).days
        candidates = []
        for off in (base - 1, base, base + 1):
            candidates += pool(off)
        for e in evs:
            tennis = "tennis" in (e["sport_key"] or "").lower()
            best: Optional[dict] = None
            best_sc = 0.0
            for g in candidates:
                if tennis:
                    sc = (1 if _surname(e["home"]) == _surname(g["home"]) else 0)
                    sc += (1 if _surname(e["away"]) == _surname(g["away"]) else 0)
                    sc /= 2.0
                else:
                    sc = (_sim(_tokens(e["home"]), _tokens(g["home"]))
                          + _sim(_tokens(e["away"]), _tokens(g["away"]))) / 2.0
                if sc > best_sc:
                    best_sc, best = sc, g
            if best and best_sc >= threshold:
                with get_db() as db:
                    db.execute("UPDATE events SET status='finished', home_score=?, away_score=?, "
                               "updated_at=datetime('now') WHERE id=?",
                               (best["hs"], best["as"], e["id"]))
                    db.commit()
                scored += 1
    logger.info("flashscore backfill scored %s events", scored)
    return {"scored": scored}
