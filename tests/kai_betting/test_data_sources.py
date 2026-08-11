"""Tests for Kai Betting data sources and data ingestion pipeline."""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    import core.kai_betting.db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = path
    db_mod.init_db()

    yield path

    db_mod.DB_PATH = original
    try:
        os.unlink(path)
    except OSError:
        pass


# ── SPORT_KEY_MAP Tests ────────────────────────────────────────────────────

class TestSportKeyMapping:
    """Verify bidirectional sport key lookups."""

    def test_odds_sport_keys_for_football(self):
        from core.kai_betting.data_sources.odds_api import odds_sport_keys_for
        keys = odds_sport_keys_for("football")
        assert "soccer_epl" in keys
        assert "soccer_uefa_champs_league" in keys
        assert len(keys) > 10  # Football has the most mappings

    def test_odds_sport_keys_for_basketball(self):
        from core.kai_betting.data_sources.odds_api import odds_sport_keys_for
        keys = odds_sport_keys_for("basketball")
        assert "basketball_nba" in keys
        assert "basketball_euroleague" in keys

    def test_odds_sport_keys_for_tennis(self):
        from core.kai_betting.data_sources.odds_api import odds_sport_keys_for
        keys = odds_sport_keys_for("tennis")
        assert "tennis_atp" in keys
        assert "tennis_wta" in keys

    def test_odds_sport_keys_unknown_sport(self):
        from core.kai_betting.data_sources.odds_api import odds_sport_keys_for
        keys = odds_sport_keys_for("formula1")
        assert keys == []

    def test_kai_sport_for_soccer_epl(self):
        from core.kai_betting.data_sources.odds_api import kai_sport_for
        assert kai_sport_for("soccer_epl") == "football"

    def test_kai_sport_for_unknown_key(self):
        from core.kai_betting.data_sources.odds_api import kai_sport_for
        assert kai_sport_for("racing_f1") is None

    def test_popular_no_missed_sports(self):
        """Verify all major odds sport keys map to valid Kai sports."""
        from core.kai_betting.data_sources.odds_api import SPORT_KEY_MAP, kai_sport_for
        valid_sports = {"football", "basketball", "tennis", "baseball",
                        "ice_hockey", "american_football", "rugby", "cricket"}
        for odds_key, kai_sport in SPORT_KEY_MAP.items():
            assert kai_sport in valid_sports, f"{odds_key} → {kai_sport} not in valid sports"


# ── Selection Mapping Tests ────────────────────────────────────────────────

class TestSelectionMapping:
    """Verify outcome name → Kai selection string mapping."""

    def test_h2h_home_team(self):
        from core.kai_betting.data_sources.odds_api import _selection_from_outcome_name
        result = _selection_from_outcome_name(
            "Manchester United", "match_result",
            "Manchester United", "Liverpool"
        )
        assert result == "home"

    def test_h2h_away_team(self):
        from core.kai_betting.data_sources.odds_api import _selection_from_outcome_name
        result = _selection_from_outcome_name(
            "Liverpool", "match_result",
            "Manchester United", "Liverpool"
        )
        assert result == "away"

    def test_h2h_draw(self):
        from core.kai_betting.data_sources.odds_api import _selection_from_outcome_name
        result = _selection_from_outcome_name(
            "Draw", "match_result",
            "Manchester United", "Liverpool"
        )
        assert result == "draw"

    def test_totals_over(self):
        from core.kai_betting.data_sources.odds_api import _selection_from_outcome_name
        result = _selection_from_outcome_name(
            "Over", "over_under", "Home", "Away"
        )
        assert result == "over"

    def test_totals_under(self):
        from core.kai_betting.data_sources.odds_api import _selection_from_outcome_name
        result = _selection_from_outcome_name(
            "Under", "over_under", "Home", "Away"
        )
        assert result == "under"


# ── OddsAPISource Tests ────────────────────────────────────────────────────

class TestOddsAPISource:
    """Verify Odds API client initialization and rate-limit tracking."""

    def test_not_configured_without_key(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="")
        assert not source.is_configured

    def test_configured_with_key(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key-123")
        assert source.is_configured

    def test_rate_limits_initial_none(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key")
        assert source.rate_limit_remaining is None
        assert source.rate_limit_total is None

    def test_has_quota_returns_false_without_refresh(self):
        """Without a DB to refresh from, has_quota defaults to False (no remaining)."""
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key")
        # rate_limit_remaining is None → has_quota() tries refresh but returns False
        # because no DB is set up for refresh_rate_limits
        with patch.object(source, 'refresh_rate_limits', return_value=None):
            source._rate_limit_remaining = 0
            assert not source.has_quota()

    def test_has_quota_returns_true_with_remaining(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key")
        source._rate_limit_remaining = 500
        assert source.has_quota()

    def test_mark_api_call_decrements(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key")
        source._rate_limit_remaining = 500
        source.mark_api_call(1)
        assert source._rate_limit_remaining == 499

    def test_mark_api_call_does_not_go_negative(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="test-key")
        source._rate_limit_remaining = 0
        source.mark_api_call(1)
        assert source._rate_limit_remaining == 0

    def test_fetch_events_skips_without_key(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="")
        result = source.fetch_events(["soccer_epl"])
        assert result == {}

    def test_fetch_odds_skips_without_key(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="")
        result = source.fetch_odds(["soccer_epl"])
        assert result == {}

    def test_fetch_scores_skips_without_key(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource
        source = OddsAPISource(api_key="")
        result = source.fetch_scores(["soccer_epl"])
        assert result == {}


# ── DataIngestionManager Tests ─────────────────────────────────────────────

class TestDataIngestionManager:
    """Verify ingestion manager initialization and graceful degradation."""

    def test_init_creates_odds_api_source(self):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        assert mgr._primary is not None
        assert mgr._engine is not None

    def test_not_configured_without_key(self):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        # Clear env and secrets to simulate no-key scenario
        mgr._primary._api_key = ""
        assert not mgr.is_configured

    def test_refresh_events_skips_when_not_configured(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        mgr._primary._api_key = ""
        result = mgr.refresh_events()
        assert result["status"] == "skipped"
        assert "ODDS_API_IO_KEY" in result["reason"]

    def test_refresh_sync_skips_when_not_configured(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        mgr._primary._api_key = ""
        result = mgr.refresh_sync()
        assert result["status"] == "skipped"
        assert "ODDS_API_IO_KEY" in result["reason"]

    def test_get_active_sports_defaults(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        sports = mgr._get_active_sports()
        assert "football" in sports
        assert "basketball" in sports
        assert "tennis" in sports

    def test_get_active_sports_custom(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        mgr = DataIngestionManager()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO betting_config (key, value) VALUES ('active_sports_for_sync', 'football,baseball')"
            )
            db.commit()
        sports = mgr._get_active_sports()
        assert sports == ["football", "baseball"]

    def test_should_run_never_run_before(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        result = mgr._should_run("test_key_never_set", 3600)
        assert result is True

    def test_should_run_recently_run(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        from datetime import datetime, timezone
        mgr = DataIngestionManager()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO betting_config (key, value) VALUES (?, ?)",
                ("test_key_recent", datetime.now(timezone.utc).isoformat())
            )
            db.commit()
        result = mgr._should_run("test_key_recent", 3600)
        assert result is False

    def test_config_int_default(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        mgr = DataIngestionManager()
        val = mgr._config_int("nonexistent_key", 42)
        assert val == 42

    def test_config_int_from_db(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        mgr = DataIngestionManager()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO betting_config (key, value) VALUES ('test_interval', '120')"
            )
            db.commit()
        val = mgr._config_int("test_interval", 60)
        assert val == 120


# ── DB Upsert Helpers ──────────────────────────────────────────────────────

class TestUpsertHelpers:
    """Verify the upsert functions work correctly."""

    def test_upsert_team_new(self, temp_db):
        from core.kai_betting.db import get_db, upsert_team
        with get_db() as db:
            team_id = upsert_team(db, 1, "Manchester United")
            assert team_id > 0

            # Verify it was created
            row = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
            assert row["name"] == "Manchester United"
            assert row["key"] == "manchester_united"

    def test_upsert_team_duplicate(self, temp_db):
        from core.kai_betting.db import get_db, upsert_team
        with get_db() as db:
            first_id = upsert_team(db, 1, "Manchester United")
            second_id = upsert_team(db, 1, "Manchester United")
            assert first_id == second_id

    def test_upsert_league_new(self, temp_db):
        from core.kai_betting.db import get_db, upsert_league
        with get_db() as db:
            league_id = upsert_league(db, 1, "epl", "English Premier League")
            assert league_id > 0

            row = db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
            assert row["name"] == "English Premier League"
            assert row["key"] == "epl"

    def test_upsert_league_duplicate(self, temp_db):
        from core.kai_betting.db import get_db, upsert_league
        with get_db() as db:
            first_id = upsert_league(db, 1, "epl", "English Premier League")
            second_id = upsert_league(db, 1, "epl", "English Premier League")
            assert first_id == second_id

    def test_upsert_event_new(self, temp_db):
        from core.kai_betting.db import get_db, upsert_event, upsert_team
        with get_db() as db:
            home_id = upsert_team(db, 1, "Manchester United")
            away_id = upsert_team(db, 1, "Liverpool")
            event_id = upsert_event(
                db, sport_id=1, external_id="ext-123",
                home_team_id=home_id, away_team_id=away_id,
                event_time="2026-08-15T19:00:00Z",
            )
            assert event_id > 0

            row = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            assert row["external_id"] == "ext-123"
            assert row["status"] == "scheduled"

    def test_upsert_event_duplicate(self, temp_db):
        from core.kai_betting.db import get_db, upsert_event, upsert_team
        with get_db() as db:
            home_id = upsert_team(db, 1, "Manchester United")
            away_id = upsert_team(db, 1, "Liverpool")
            first_id = upsert_event(
                db, 1, "ext-dup", home_id, away_id, "2026-08-15T19:00:00Z",
            )
            second_id = upsert_event(
                db, 1, "ext-dup", home_id, away_id, "2026-08-16T19:00:00Z",
            )
            # INSERT OR REPLACE creates a new rowid, but there's only one row
            # for (sport_id, external_id). Verify count = 1.
            count = db.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE sport_id = 1 AND external_id = 'ext-dup'"
            ).fetchone()
            assert count["cnt"] == 1
            # The returned ID is the current (possibly new) rowid
            assert second_id > 0

    def test_upsert_team_different_sports(self, temp_db):
        """Same team name in different sports gets different IDs."""
        from core.kai_betting.db import get_db, upsert_team
        with get_db() as db:
            football_team = upsert_team(db, 1, "Kings")  # sport_id=1 (football)
            basketball_team = upsert_team(db, 2, "Kings")  # sport_id=2 (basketball)
            assert football_team != basketball_team


# ── Data Source Registry ───────────────────────────────────────────────────

class TestDataSourcesRegistry:
    """Verify the data sources module exports correctly."""

    def test_registry_contains_odds_api(self):
        from core.kai_betting.data_sources import DATA_SOURCES, OddsAPISource
        assert "odds_api" in DATA_SOURCES
        assert DATA_SOURCES["odds_api"] is OddsAPISource

    def test_odds_api_source_importable(self):
        from core.kai_betting.data_sources.odds_api import OddsAPISource, SPORT_KEY_MAP
        assert isinstance(SPORT_KEY_MAP, dict)
        assert len(SPORT_KEY_MAP) > 50


# ── Ingestion Event Processing ─────────────────────────────────────────────

class TestIngestionEventProcessing:
    """Verify event ingestion with mock data."""

    def test_ingest_event_new(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        mgr = DataIngestionManager()
        evt = {
            "id": "abc123",
            "home_team": "Manchester United",
            "away_team": "Liverpool",
            "commence_time": "2026-08-15T19:00:00Z",
        }
        with get_db() as db:
            result = mgr._ingest_event(db, sport_id=1, odds_key="soccer_epl", evt=evt)
            assert result == 1  # new event

    def test_ingest_event_existing(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        mgr = DataIngestionManager()
        evt = {
            "id": "abc123",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-08-16T19:00:00Z",
        }
        with get_db() as db:
            result1 = mgr._ingest_event(db, sport_id=1, odds_key="soccer_epl", evt=evt)
            result2 = mgr._ingest_event(db, sport_id=1, odds_key="soccer_epl", evt=evt)
            assert result1 == 1  # new
            assert result2 == 0  # updated

    def test_ingest_event_no_external_id(self, temp_db):
        from core.kai_betting.data_ingestion import DataIngestionManager
        from core.kai_betting.db import get_db
        mgr = DataIngestionManager()
        evt = {
            "id": "",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
        }
        with get_db() as db:
            result = mgr._ingest_event(db, sport_id=1, odds_key="soccer_epl", evt=evt)
            assert result == 0  # skipped


# ── Config Module Tests ────────────────────────────────────────────────────

class TestBettingConfig:
    """Verify the module config file is valid."""

    def test_config_file_exists(self):
        import json
        import os
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "modules", "kai-betting.json"
        )
        assert os.path.exists(config_path), f"Config file missing at {config_path}"

    def test_config_is_valid_json(self):
        import json
        import os
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "modules", "kai-betting.json"
        )
        with open(config_path) as f:
            data = json.load(f)
        assert "data_sources" in data
        assert "refresh" in data
        assert "odds_api" in data["data_sources"]
        assert data["data_sources"]["odds_api"]["rate_limit_monthly"] == 500
