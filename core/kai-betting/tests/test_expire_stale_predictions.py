"""Covers KaiBettingWorkers.expire_stale_predictions().

auto_settle_finished() only settles predictions whose linked event reaches
status='finished' with scores. A prediction whose event never gets there
(e.g. broken/empty event_time — see _ingest_event_v3 placeholder-name fix —
blocking event_is_started() and cross-provider settlement matching) stays
'published' forever, and telegram_bot.send_picks_to() keeps re-sending it
as a "pick" indefinitely regardless of age. expire_stale_predictions() is
the safety net: anything still unsettled after max_age_hours is stale.
"""

from datetime import datetime, timezone, timedelta

from core.kai_betting.workers import KaiBettingWorkers
from core.kai_betting.db import get_db, upsert_team, upsert_event


def _insert_prediction(db, event_id, sport_id, status, created_at):
    cursor = db.execute("""
        INSERT INTO predictions (event_id, sport_id, market_type, market_name,
            selection, bookmaker_odds, estimated_probability, confidence,
            risk_score, data_quality, reasoning, status, created_at)
        VALUES (?, ?, 'match_result', 'Match Result', 'home', 1.5,
            0.7, 85.5, 10.0, 90.0, 'real reasoning', ?, ?)
    """, (event_id, sport_id, status, created_at))
    db.commit()
    return cursor.lastrowid


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_expires_old_unsettled_predictions(fresh_db):
    workers = KaiBettingWorkers.__new__(KaiBettingWorkers)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home")
        away_id = upsert_team(db, sport_id, "Away")
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id, "")
        old_pred = _insert_prediction(db, event_id, sport_id, "published", _iso(hours_ago=72))

    result = workers.expire_stale_predictions(max_age_hours=48)
    assert result["expired"] == 1

    with get_db() as db:
        row = db.execute("SELECT status FROM predictions WHERE id = ?", (old_pred,)).fetchone()
    assert row["status"] == "expired"


def test_leaves_recent_unsettled_predictions_alone(fresh_db):
    workers = KaiBettingWorkers.__new__(KaiBettingWorkers)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home")
        away_id = upsert_team(db, sport_id, "Away")
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id, "")
        recent_pred = _insert_prediction(db, event_id, sport_id, "published", _iso(hours_ago=2))

    result = workers.expire_stale_predictions(max_age_hours=48)
    assert result["expired"] == 0

    with get_db() as db:
        row = db.execute("SELECT status FROM predictions WHERE id = ?", (recent_pred,)).fetchone()
    assert row["status"] == "published"


def test_leaves_already_settled_predictions_alone(fresh_db):
    workers = KaiBettingWorkers.__new__(KaiBettingWorkers)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home")
        away_id = upsert_team(db, sport_id, "Away")
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id, "")
        won_pred = _insert_prediction(db, event_id, sport_id, "won", _iso(hours_ago=72))

    result = workers.expire_stale_predictions(max_age_hours=48)
    assert result["expired"] == 0

    with get_db() as db:
        row = db.execute("SELECT status FROM predictions WHERE id = ?", (won_pred,)).fetchone()
    assert row["status"] == "won"


def test_expires_across_all_unsettled_statuses(fresh_db):
    workers = KaiBettingWorkers.__new__(KaiBettingWorkers)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Home")
        away_id = upsert_team(db, sport_id, "Away")
        event_id = upsert_event(db, sport_id, "ext-1", home_id, away_id, "")
        ids = [
            _insert_prediction(db, event_id, sport_id, status, _iso(hours_ago=72))
            for status in ("pending", "published", "quality_check", "approved")
        ]

    result = workers.expire_stale_predictions(max_age_hours=48)
    assert result["expired"] == 4

    with get_db() as db:
        for pred_id in ids:
            row = db.execute("SELECT status FROM predictions WHERE id = ?", (pred_id,)).fetchone()
            assert row["status"] == "expired"
