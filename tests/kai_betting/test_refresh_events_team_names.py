"""Covers refresh_events() not silently reverting events to placeholder
"Home"/"Away" team names.

refresh_events() calls _ingest_event() (the legacy "The Odds API v4" format
handler, which reads evt["home_team"]/evt["away_team"]) on every event
returned by OddsAPIioSource.fetch_events() — but that source's events use
the v3 schema key names "home"/"away" (see _ingest_event_v3, which is built
for this exact schema and already covered by
test_ingest_event_v3_team_names.py). Since evt never actually has
"home_team"/"away_team", _ingest_event() always falls back to writing the
"Home"/"Away" placeholder team names, no matter what fetch_events() actually
returned.

Because upsert_event() unconditionally overwrites home_team_id/away_team_id
on every call, this doesn't just affect brand-new events: refresh_events()
runs on its own periodic interval independent of _sync_odds_v3() (which
correctly resolves and writes real team names once odds are available), so
a later refresh_events() cycle silently reverts an already-correctly-named
event back to "Home"/"Away" — even though its predictions' reasoning text
still shows the real matchup. This is what produced literal "Home vs Away"
entries surfacing in live odds groups.

refresh_events() should use _ingest_event_v3() (schema-correct, and already
proven to prefer real evt-provided names over the placeholder) instead of
the legacy _ingest_event().
"""

from unittest.mock import patch

from core.kai_betting.data_ingestion import DataIngestionManager
from core.kai_betting.db import get_db


def _teams(db, external_id):
    row = db.execute("""
        SELECT ht.name home_name, at.name away_name
        FROM events e
        JOIN teams ht ON ht.id = e.home_team_id
        JOIN teams at ON at.id = e.away_team_id
        WHERE e.external_id = ?
    """, (external_id,)).fetchone()
    return dict(row)


def test_refresh_events_preserves_real_team_names_from_v3_schema(fresh_db, monkeypatch):
    monkeypatch.setenv("ODDS_API_IO_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    mgr._primary = None  # replaced by is_configured patch below

    evt = {
        "id": "ev-refresh-1",
        "home": "Real Madrid",
        "away": "Barcelona",
        "date": "2026-08-20T18:00:00Z",
        "status": "pending",
        "league": {},
    }

    with patch(
        "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.is_configured",
        new_callable=lambda: property(lambda self: True),
    ), patch(
        "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.fetch_events",
        return_value={"football": [evt]},
    ):
        from core.kai_betting.data_sources.odds_api_io import OddsAPIioSource
        mgr._primary = OddsAPIioSource()
        result = mgr.refresh_events()

    assert result["status"] == "ok"

    with get_db() as db:
        names = _teams(db, "ev-refresh-1")

    assert names["home_name"] == "Real Madrid"
    assert names["away_name"] == "Barcelona"


def test_refresh_events_does_not_revert_previously_resolved_names(fresh_db, monkeypatch):
    """The actual production bug: a later refresh_events() cycle must not
    stomp an event that _sync_odds_v3() already gave real team names to."""
    monkeypatch.setenv("ODDS_API_IO_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    from core.kai_betting.data_sources.odds_api_io import OddsAPIioSource

    with patch(
        "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.is_configured",
        new_callable=lambda: property(lambda self: True),
    ):
        mgr._primary = OddsAPIioSource()

        with get_db() as db:
            sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
            mgr._ingest_event_v3(
                db, sport_id,
                {"id": "ev-refresh-2", "league": {}},
                home_name="Real Madrid", away_name="Barcelona",
                event_time="2026-08-20T18:00:00Z",
            )
            db.commit()

        # fetch_events()'s list response omits home/away for this event, as
        # it does for some sports/providers.
        evt = {"id": "ev-refresh-2", "status": "pending", "league": {}}

        with patch(
            "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.fetch_events",
            return_value={"football": [evt]},
        ):
            result = mgr.refresh_events()

        assert result["status"] == "ok"

        with get_db() as db:
            names = _teams(db, "ev-refresh-2")

    assert names["home_name"] == "Real Madrid"
    assert names["away_name"] == "Barcelona"
