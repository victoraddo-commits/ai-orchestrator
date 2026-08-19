"""Covers _ingest_event_v3 not silently writing placeholder "Home"/"Away"
team names when evt's own home/away/date keys are missing.

fetch_events()'s list response omits home/away/date for some sports (e.g.
tennis) even though fetch_odds()'s per-event response has them. The caller
in _ingest_odds_api_io() already resolves the real names via
odds_data.get("home", evt.get("home", "")) for building predictions, but
used to call _ingest_event_v3(db, sport_id, evt) without passing that
resolution through — so the event/team rows still got the "Home"/"Away"
fallback default baked into _ingest_event_v3 itself, even though the real
name was available. This left events permanently stuck with placeholder
team names in the DB (visible everywhere the events table is joined,
e.g. odds-group selections) despite reasoning text having the real matchup.
"""

from core.kai_betting.data_ingestion import DataIngestionManager
from core.kai_betting.db import get_db


def _teams(db, event_id):
    row = db.execute("""
        SELECT ht.name home_name, at.name away_name, e.event_time
        FROM events e
        JOIN teams ht ON ht.id = e.home_team_id
        JOIN teams at ON at.id = e.away_team_id
        WHERE e.id = ?
    """, (event_id,)).fetchone()
    return dict(row)


def test_ingest_event_v3_uses_evt_names_when_present(fresh_db):
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    evt = {"id": "ev-1", "home": "Real Madrid", "away": "Barcelona",
           "date": "2026-08-20T18:00:00Z", "league": {}}

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
        event_pk = mgr._ingest_event_v3(db, sport_id, evt)
        db.commit()

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, "ev-1")
        ).fetchone()
        names = _teams(db, event_row["id"])

    assert names["home_name"] == "Real Madrid"
    assert names["away_name"] == "Barcelona"


def test_ingest_event_v3_falls_back_to_evt_when_no_override_and_evt_is_empty(fresh_db):
    """Old behavior preserved for the case where nothing at all is available."""
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    evt = {"id": "ev-2", "league": {}}

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
        mgr._ingest_event_v3(db, sport_id, evt)
        db.commit()

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, "ev-2")
        ).fetchone()
        names = _teams(db, event_row["id"])

    assert names["home_name"] == "Home"
    assert names["away_name"] == "Away"


def test_ingest_event_v3_prefers_override_over_evt_placeholder(fresh_db):
    """The bug: evt lacks home/away (as fetch_events omits them for some
    sports), but the caller already resolved real names from fetch_odds()'s
    response and must be able to pass them through instead of getting stuck
    with "Home"/"Away"."""
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    evt = {"id": "ev-3", "league": {}}  # no home/away/date keys at all

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'tennis'").fetchone()["id"]
        mgr._ingest_event_v3(
            db, sport_id, evt,
            home_name="Novak Djokovic", away_name="Carlos Alcaraz",
            event_time="2026-08-21T14:00:00Z",
        )
        db.commit()

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, "ev-3")
        ).fetchone()
        names = _teams(db, event_row["id"])

    assert names["home_name"] == "Novak Djokovic"
    assert names["away_name"] == "Carlos Alcaraz"
    assert names["event_time"] == "2026-08-21T14:00:00Z"


def test_ingest_event_v3_override_empty_string_falls_back_to_evt(fresh_db):
    """Mirrors the real call site: odds_data.get("home", evt.get("home", ""))
    resolves to "" (not None) when truly nothing is available anywhere —
    that must still fall back to evt's own default, not store an empty name."""
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    evt = {"id": "ev-4", "league": {}}

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
        mgr._ingest_event_v3(db, sport_id, evt, home_name="", away_name="", event_time="")
        db.commit()

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, "ev-4")
        ).fetchone()
        names = _teams(db, event_row["id"])

    assert names["home_name"] == "Home"
    assert names["away_name"] == "Away"
