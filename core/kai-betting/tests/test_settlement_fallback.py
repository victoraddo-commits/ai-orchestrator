"""Tests for the SportsGameOdds settlement fallback.

Covers the pieces that let predictions attached to PRIMARY-provider events
get settled from SportsGameOdds' finalized results when the legacy scores
provider (odds_api) is unconfigured or out of quota:
  - extract_result(): pull a final score out of a raw SGO event dict
  - DataIngestionManager._find_matching_event(): cross-provider team+time match
  - DataIngestionManager._sync_results_fallback_sgo(): end-to-end settlement
  - OddsAPISource-quota gating that triggers the fallback in refresh_sync()
"""

from unittest.mock import patch

from core.kai_betting.data_sources.sportsgameodds import extract_result
from core.kai_betting.data_ingestion import DataIngestionManager
from core.kai_betting.db import get_db, upsert_team, upsert_event


def _finalized_event(home="Home FC", away="Away FC", home_pts=2, away_pts=1,
                      league_id="NBA", starts_at="2026-08-10T18:00:00Z"):
    return {
        "leagueID": league_id,
        "status": {"finalized": True, "startsAt": starts_at},
        "teams": {
            "home": {"names": {"long": home}},
            "away": {"names": {"long": away}},
        },
        "results": {
            "game": {
                "home": {"points": home_pts},
                "away": {"points": away_pts},
            }
        },
    }


# ── extract_result() ─────────────────────────────────────────────────────

def test_extract_result_returns_scores_for_finalized_event():
    evt = _finalized_event(home_pts=3, away_pts=1)
    assert extract_result(evt) == {"home_score": 3, "away_score": 1}


def test_extract_result_none_when_not_finalized():
    evt = _finalized_event()
    evt["status"]["finalized"] = False
    assert extract_result(evt) is None


def test_extract_result_none_when_scores_missing():
    evt = _finalized_event()
    evt["results"]["game"]["home"] = {}
    assert extract_result(evt) is None


# ── DataIngestionManager._find_matching_event() ──────────────────────────

def test_find_matching_event_matches_within_time_window(fresh_db):
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                     "2026-08-10T18:00:00Z")

        matched = mgr._find_matching_event(
            db, sport_id, "Home FC", "Away FC", "2026-08-10T18:30:00Z"
        )
        assert matched is not None
        assert matched["external_id"] == "primary-ext-1"


def test_find_matching_event_excludes_finished_events(fresh_db):
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                     "2026-08-10T18:00:00Z", status="finished")

        matched = mgr._find_matching_event(
            db, sport_id, "Home FC", "Away FC", "2026-08-10T18:00:00Z"
        )
        assert matched is None


def test_find_matching_event_matches_across_spelling_variants(fresh_db):
    """Providers spell the same club differently — accents, periods,
    abbreviation punctuation — e.g. 'CF Montreal' (odds_api_io) vs
    'CF Montréal' (SportsGameOdds), or 'D.C. United' vs 'DC United'.
    Real production data hit exactly this: predictions attached to a
    primary-provider event never matched because of an exact string
    comparison."""
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "CF Montreal")
        away_id = upsert_team(db, sport_id, "D.C. United")
        upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                     "2026-08-10T18:00:00Z")

        matched = mgr._find_matching_event(
            db, sport_id, "CF Montréal", "DC United", "2026-08-10T18:30:00Z"
        )
        assert matched is not None
        assert matched["external_id"] == "primary-ext-1"


def test_find_matching_event_outside_time_window_returns_none(fresh_db):
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                     "2026-08-10T18:00:00Z")

        matched = mgr._find_matching_event(
            db, sport_id, "Home FC", "Away FC", "2026-08-11T18:00:00Z"
        )
        assert matched is None


# ── DataIngestionManager._sync_results_fallback_sgo() ────────────────────

def test_sync_results_fallback_sgo_settles_primary_event(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        event_id = upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                                "2026-08-10T18:00:00Z")
        db.execute("""
            INSERT INTO predictions (event_id, sport_id, market_type, market_name,
                selection, estimated_probability, confidence, status)
            VALUES (?, ?, 'match_result', 'Match Result', 'home', 0.7, 75.0, 'published')
        """, (event_id, sport_id))
        db.commit()

    evt = _finalized_event(home="Home FC", away="Away FC", home_pts=2, away_pts=1)

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_results_fallback_sgo()

    assert result["status"] == "ok"
    assert result["events_matched"] == 1
    assert result["predictions_settled"] == 1

    with get_db() as db:
        event_row = db.execute(
            "SELECT status, home_score, away_score FROM events WHERE external_id = 'primary-ext-1'"
        ).fetchone()
        assert event_row["status"] == "finished"
        assert event_row["home_score"] == 2
        assert event_row["away_score"] == 1

        pred_row = db.execute(
            "SELECT status FROM predictions WHERE event_id = ?", (event_id,)
        ).fetchone()
        assert pred_row["status"] == "won"


def test_sync_results_fallback_sgo_skipped_without_api_key(fresh_db, monkeypatch):
    monkeypatch.delenv("SPORTSGAMEODDS_API_KEY", raising=False)
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    result = mgr._sync_results_fallback_sgo()
    assert result["status"] == "skipped"


# ── Legacy quota gating (trigger condition for the fallback) ─────────────

def test_sync_results_legacy_skips_when_quota_exhausted(fresh_db, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    with patch(
        "core.kai_betting.data_sources.odds_api.OddsAPISource.has_quota",
        return_value=False,
    ):
        result = mgr._sync_results_legacy()

    assert result == {"status": "skipped", "reason": "legacy quota exhausted"}
