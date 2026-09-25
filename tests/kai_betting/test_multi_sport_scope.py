"""Multi-sport coverage: provider-slug mapping and honest ``no_source`` reporting.

Locks in the 2026-09-25 widening: every approved, source-backed sport maps to a
real Odds-API.io slug and is in the default sync list; horse racing is approved
but has no feed and must report ``no_source`` rather than silently returning
nothing or fabricating fixtures.
"""

from core.kai_betting import scope
from core.kai_betting.data_ingestion import DataIngestionManager, source_status
from core.kai_betting.data_sources.odds_api_io import slug_for_kai
from core.kai_betting.data_sources.horse_racing import HorseRacingSource


def test_source_backed_sports_map_to_provider_slugs():
    for sport in scope.SPORTS_WITH_SOURCE:
        assert slug_for_kai(sport), f"no Odds-API.io slug for {sport}"


def test_horse_racing_has_no_provider_slug():
    assert slug_for_kai("horse_racing") is None
    assert "horse_racing" not in scope.SPORTS_WITH_SOURCE


def test_horse_racing_adapter_reports_no_source():
    src = HorseRacingSource()
    st = src.provider_status
    assert src.is_configured is False
    assert st["status"] == "no_source"
    assert st["connected"] is False
    assert src.fetch_events() == {}
    assert src.fetch_odds(1) is None


def test_source_status_marks_horse_racing_no_source():
    status = source_status()
    assert status["sports"]["horse_racing"] == "no_source"
    assert status["sports"]["football"] == "available"
    assert status["providers"]["horse_racing"]["status"] == "no_source"


def test_default_active_sports_include_new_sports(fresh_db):
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    active = mgr._get_active_sports()
    assert set(active) == set(scope.DEFAULT_SYNC_SPORTS)
    for s in ("ice_hockey", "baseball", "american_football", "rugby",
              "volleyball", "handball", "cricket", "esports", "darts",
              "mma", "boxing", "table_tennis", "snooker"):
        assert s in active, f"{s} missing from default sync list"
    assert "horse_racing" not in active  # approved, but no source


def test_default_config_seeds_new_sports(fresh_db):
    from core.kai_betting.db import get_db
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM betting_config WHERE key = 'active_sports_for_sync'"
        ).fetchone()
    sports = {s.strip() for s in row["value"].split(",")}
    assert set(scope.DEFAULT_SYNC_SPORTS) <= sports


def test_sports_table_has_every_approved_sport(fresh_db):
    from core.kai_betting.db import get_db
    with get_db() as db:
        keys = {r["key"] for r in db.execute("SELECT key FROM sports")}
    missing = set(scope.APPROVED_SPORTS) - keys
    assert not missing, f"approved sports not seeded: {sorted(missing)}"


def test_refresh_events_ingests_multiple_sports(fresh_db, monkeypatch):
    """One sync run loads events for more than one sport.

    Deterministic stand-in for the live Odds-API.io sync (which requires a
    valid key): the real ``refresh_events`` -> ``_ingest_event_v3`` path is
    exercised with a stubbed provider payload covering four sports.
    """
    from unittest.mock import patch
    from core.kai_betting.data_sources.odds_api_io import OddsAPIioSource
    from core.kai_betting.db import get_db

    fetch_map = {
        "ice-hockey": [{"id": "ev-ice-1", "home": "Bruins", "away": "Rangers",
                        "date": "2026-10-01T18:00:00Z", "status": "pending",
                        "league": {"name": "NHL", "slug": "nhl"}}],
        "baseball": [{"id": "ev-mlb-1", "home": "Yankees", "away": "Red Sox",
                      "date": "2026-10-01T18:00:00Z", "status": "pending",
                      "league": {"name": "MLB", "slug": "mlb"}}],
        "cricket": [{"id": "ev-ipl-1", "home": "Mumbai", "away": "Chennai",
                     "date": "2026-10-01T14:00:00Z", "status": "pending",
                     "league": {"name": "Indian Premier League", "slug": "ipl"}}],
        "darts": [{"id": "ev-darts-1", "home": "Humphries", "away": "Littler",
                   "date": "2026-10-01T19:00:00Z", "status": "pending",
                   "league": {"name": "PDC World Championship",
                              "slug": "pdc-world-championship"}}],
    }

    monkeypatch.setenv("ODDS_API_IO_KEY", "fake-key")
    with patch(
        "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.is_configured",
        new_callable=lambda: property(lambda self: True),
    ), patch(
        "core.kai_betting.data_sources.odds_api_io.OddsAPIioSource.fetch_events",
        return_value=fetch_map,
    ):
        mgr = DataIngestionManager()
        result = mgr.refresh_events()

    assert result["status"] == "ok"
    assert result["new_events"] >= 4, result

    with get_db() as db:
        rows = db.execute(
            "SELECT s.key key, COUNT(e.id) n FROM events e "
            "JOIN sports s ON s.id = e.sport_id GROUP BY s.key HAVING n > 0"
        ).fetchall()
    by_sport = {r["key"]: r["n"] for r in rows}
    for sport in ("ice_hockey", "baseball", "cricket", "darts"):
        assert by_sport.get(sport, 0) >= 1, by_sport


def test_source_status_degrades_gracefully_without_provider_status():
    status = source_status()
    # odds_api and sportsgameodds expose is_configured but not provider_status;
    # the registry must report them honestly rather than surfacing AttributeError.
    for name in ("odds_api", "sportsgameodds"):
        entry = status["providers"][name]
        assert entry["status"] != "error", entry
        assert entry["status"] == "unconfigured", entry
    for name, entry in status["providers"].items():
        assert "status" in entry, f"{name} missing status"
        assert entry["connected"] in (True, False)


def test_sources_endpoint_reports_no_source(client):
    r = client.get("/api/betting/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["sports"]["horse_racing"] == "no_source"
    assert data["sports"]["football"] == "available"
    assert data["providers"]["horse_racing"]["status"] == "no_source"
