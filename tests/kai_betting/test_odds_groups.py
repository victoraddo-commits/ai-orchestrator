"""Tests for odds-group generation not orphaning/duplicating predictions.

OddsEngine._get_candidates() reads real, already event-linked predictions
out of the DB. api._ensure_prediction_saved() used to always INSERT a fresh
copy for each selection — dropping event_id (so the copy could never be
settled) and creating a new duplicate row every time a group regenerated.
These tests cover the fix: reuse the source prediction's row instead.
"""

from core.kai_betting.api import _ensure_prediction_saved
from core.kai_betting.db import get_db, upsert_team, upsert_event
from core.kai_betting.models import PredictionResult
from core.kai_betting.odds_engine import OddsEngine, RISK_THRESHOLDS


def _insert_real_prediction(db, event_id, sport_id):
    cursor = db.execute("""
        INSERT INTO predictions (event_id, sport_id, market_type, market_name,
            selection, bookmaker_odds, estimated_probability, confidence,
            risk_score, data_quality, reasoning, status)
        VALUES (?, ?, 'match_result', 'Match Result', 'home', 1.5,
            0.7, 75.0, 10.0, 90.0, 'real reasoning', 'published')
    """, (event_id, sport_id))
    db.commit()
    return cursor.lastrowid


def _make_result(source_prediction_id):
    return PredictionResult(
        source_prediction_id=source_prediction_id,
        sport_key="basketball",
        league_key=None,
        market_type="match_result",
        market_name="Match Result",
        selection="home",
        estimated_probability=0.7,
        bookmaker_odds=1.5,
        confidence=75.0,
        reasoning="real reasoning",
    )


def test_ensure_prediction_saved_reuses_existing_row_with_source_id(fresh_db):
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        # Far-future start so event_is_started() never filters it out
        # (a hard-coded 2026 date became stale and silently dropped the row).
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id,
                                 "2099-01-01T18:00:00Z")
        pred_id = _insert_real_prediction(db, event_id, sport_id)

        result = _make_result(source_prediction_id=pred_id)
        saved_id = _ensure_prediction_saved(db, result)

        assert saved_id == pred_id

        total = db.execute("SELECT count(*) c FROM predictions").fetchone()["c"]
        assert total == 1


def test_ensure_prediction_saved_does_not_duplicate_across_repeated_calls(fresh_db):
    """Simulates odds groups regenerating every few minutes and picking the
    same underlying prediction as a candidate each time."""
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        # Far-future start so event_is_started() never filters it out
        # (a hard-coded 2026 date became stale and silently dropped the row).
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id,
                                 "2099-01-01T18:00:00Z")
        pred_id = _insert_real_prediction(db, event_id, sport_id)

        result = _make_result(source_prediction_id=pred_id)
        for _ in range(5):
            saved_id = _ensure_prediction_saved(db, result)
            assert saved_id == pred_id

        total = db.execute("SELECT count(*) c FROM predictions").fetchone()["c"]
        assert total == 1


def test_ensure_prediction_saved_preserves_event_id(fresh_db):
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        # Far-future start so event_is_started() never filters it out
        # (a hard-coded 2026 date became stale and silently dropped the row).
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id,
                                 "2099-01-01T18:00:00Z")
        pred_id = _insert_real_prediction(db, event_id, sport_id)

        result = _make_result(source_prediction_id=pred_id)
        saved_id = _ensure_prediction_saved(db, result)

        row = db.execute("SELECT event_id FROM predictions WHERE id = ?", (saved_id,)).fetchone()
        assert row["event_id"] == event_id


def test_get_candidates_carries_source_prediction_id(fresh_db):
    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home FC")
        away_id = upsert_team(db, sport_id, "Away FC")
        # Far-future start so event_is_started() never filters it out
        # (a hard-coded 2026 date became stale and silently dropped the row).
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id,
                                 "2099-01-01T18:00:00Z")
        pred_id = _insert_real_prediction(db, event_id, sport_id)

    engine = OddsEngine()
    candidates = engine._get_candidates(
        risk_config=RISK_THRESHOLDS["moderate"],
        sport_keys=None,
        market_types=None,
        existing_predictions=None,
    )

    assert len(candidates) == 1
    assert candidates[0].source_prediction_id == pred_id
