"""Tests for Kai Betting database schema and operations."""

import pytest
import os
import tempfile
from core.kai_betting.db import init_db, get_db, DB_PATH, SCHEMA_SQL


@pytest.fixture
def fresh_db():
    """Create a fresh in-memory-like DB for testing."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Override DB_PATH temporarily
    import core.kai_betting.db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = path

    init_db()

    yield path

    # Cleanup
    db_mod.DB_PATH = original
    try:
        os.unlink(path)
    except OSError:
        pass


class TestDatabaseSchema:
    """Verify all tables are created correctly."""

    def test_all_tables_created(self, fresh_db):
        with get_db() as db:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            names = [t["name"] for t in tables]

        expected = [
            "audit_logs", "betting_config", "data_sources", "event_statistics",
            "events", "leagues", "notifications", "odds_group_selections",
            "odds_groups", "payments", "performance_metrics", "prediction_models",
            "prediction_results", "predictions", "sports", "subscription_plans",
            "subscriptions", "teams", "telegram_accounts", "user_preferences", "users",
        ]
        for name in expected:
            assert name in names, f"Table {name} missing"

    def test_sports_seeded(self, fresh_db):
        with get_db() as db:
            sports = db.execute("SELECT key, name, sort_order FROM sports ORDER BY sort_order").fetchall()

        assert len(sports) == 10
        assert sports[0]["key"] == "football"
        assert sports[0]["name"] == "⚽ Football"
        assert sports[-1]["key"] == "cricket"

    def test_subscription_plans_seeded(self, fresh_db):
        with get_db() as db:
            plans = db.execute("SELECT key, name, price FROM subscription_plans ORDER BY duration_days").fetchall()

        assert len(plans) == 3
        assert plans[0]["key"] == "daily"
        assert plans[0]["price"] == 5.0
        assert plans[-1]["key"] == "monthly"
        assert plans[-1]["price"] == 80.0

    def test_prediction_model_seeded(self, fresh_db):
        with get_db() as db:
            model = db.execute(
                "SELECT name, model_type, version FROM prediction_models WHERE name = 'kai-betting-v1'"
            ).fetchone()
        assert model is not None
        assert model["model_type"] == "ensemble"
        assert model["version"] == "1.0.0"

    def test_default_config_seeded(self, fresh_db):
        with get_db() as db:
            configs = db.execute("SELECT key, value FROM betting_config").fetchall()

        keys = {c["key"]: c["value"] for c in configs}
        assert keys.get("schema_version") == "1"
        assert keys.get("min_confidence_publish") == "50"
        assert keys.get("auto_publish") == "false"
        assert keys.get("auto_settle") == "true"

    def test_idempotent_init(self, fresh_db):
        """Multiple init_db calls should not duplicate data."""
        init_db()
        init_db()
        with get_db() as db:
            sports = db.execute("SELECT COUNT(*) as cnt FROM sports").fetchone()
            plans = db.execute("SELECT COUNT(*) as cnt FROM subscription_plans").fetchone()
        assert sports["cnt"] == 10  # No duplicates
        assert plans["cnt"] == 3


class TestSportsSchema:
    """Verify sports table constraints."""

    def test_sport_key_unique(self, fresh_db):
        with get_db() as db:
            with pytest.raises(Exception):
                db.execute("INSERT INTO sports (key, name) VALUES ('football', 'duplicate')")
                db.commit()

    def test_cascade_delete(self, fresh_db):
        """Deleting a sport should cascade to leagues."""
        with get_db() as db:
            sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
            db.execute(
                "INSERT INTO leagues (sport_id, key, name) VALUES (?, 'test-league', 'Test League')",
                (sport_id,)
            )
            db.commit()

            db.execute("DELETE FROM sports WHERE id = ?", (sport_id,))
            db.commit()

            league = db.execute("SELECT * FROM leagues WHERE key = 'test-league'").fetchone()
            assert league is None


class TestEventsSchema:
    """Verify events table constraints."""

    def test_event_creation(self, fresh_db):
        with get_db() as db:
            sport = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()
            # Create teams
            db.execute(
                "INSERT INTO teams (sport_id, key, name) VALUES (?, 'chelsea', 'Chelsea')",
                (sport["id"],)
            )
            db.execute(
                "INSERT INTO teams (sport_id, key, name) VALUES (?, 'arsenal', 'Arsenal')",
                (sport["id"],)
            )
            db.commit()

            home = db.execute("SELECT id FROM teams WHERE key = 'chelsea'").fetchone()
            away = db.execute("SELECT id FROM teams WHERE key = 'arsenal'").fetchone()

            cursor = db.execute("""
                INSERT INTO events (sport_id, home_team_id, away_team_id, event_time, status)
                VALUES (?, ?, ?, '2026-08-15T15:00:00Z', 'scheduled')
            """, (sport["id"], home["id"], away["id"],))
            db.commit()
            event_id = cursor.lastrowid

            event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            assert event["status"] == "scheduled"
            assert event["home_team_id"] == home["id"]
            assert event["away_team_id"] == away["id"]


class TestPredictionsSchema:
    """Verify predictions table constraints and indexes."""

    def test_prediction_creation(self, fresh_db):
        with get_db() as db:
            sport = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()
            # Create minimal teams and event for FK
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'a', 'A')", (sport["id"],))
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'b', 'B')", (sport["id"],))
            db.commit()
            home = db.execute("SELECT id FROM teams WHERE key = 'a'").fetchone()
            away = db.execute("SELECT id FROM teams WHERE key = 'b'").fetchone()
            db.execute(
                "INSERT INTO events (sport_id, home_team_id, away_team_id, event_time) VALUES (?, ?, ?, '2026-08-15T15:00:00Z')",
                (sport["id"], home["id"], away["id"],)
            )
            db.commit()
            event = db.execute("SELECT id FROM events LIMIT 1").fetchone()

            cursor = db.execute("""
                INSERT INTO predictions (
                    event_id, sport_id, market_type, market_name, selection,
                    estimated_probability, confidence, risk_score, data_quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event["id"], sport["id"], "match_result", "Match Result", "home", 0.55, 65.0, 25.0, 45.0))
            db.commit()

            pred = db.execute("SELECT * FROM predictions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            assert pred["status"] == "pending"
            assert pred["selection"] == "home"
            assert pred["estimated_probability"] == 0.55
            assert pred["confidence"] == 65.0

    def test_prediction_statuses(self, fresh_db):
        """Verify all prediction status values are accepted."""
        with get_db() as db:
            sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
            # Create event for FK
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'a', 'A')", (sport_id,))
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'b', 'B')", (sport_id,))
            db.commit()
            home = db.execute("SELECT id FROM teams WHERE key = 'a'").fetchone()
            away = db.execute("SELECT id FROM teams WHERE key = 'b'").fetchone()
            db.execute(
                "INSERT INTO events (sport_id, home_team_id, away_team_id, event_time) VALUES (?, ?, ?, '2026-08-15T15:00:00Z')",
                (sport_id, home["id"], away["id"],)
            )
            db.commit()
            event_id = db.execute("SELECT id FROM events LIMIT 1").fetchone()["id"]

            statuses = ["pending", "quality_check", "approved", "rejected",
                       "published", "won", "lost", "push", "void", "cancelled"]
            for status in statuses:
                cursor = db.execute("""
                    INSERT INTO predictions (event_id, sport_id, market_type, market_name, selection,
                        estimated_probability, confidence, risk_score, data_quality, status)
                    VALUES (?, ?, 'match_result', 'MR', 'home', 0.5, 50, 30, 30, ?)
                """, (event_id, sport_id, status))
                db.commit()
                pred = db.execute("SELECT status FROM predictions WHERE id = ?", (cursor.lastrowid,)).fetchone()
                assert pred["status"] == status

    def test_prediction_result_settlement(self, fresh_db):
        with get_db() as db:
            sport_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
            # Create event
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'a', 'A')", (sport_id,))
            db.execute("INSERT INTO teams (sport_id, key, name) VALUES (?, 'b', 'B')", (sport_id,))
            db.commit()
            home = db.execute("SELECT id FROM teams WHERE key = 'a'").fetchone()
            away = db.execute("SELECT id FROM teams WHERE key = 'b'").fetchone()
            db.execute(
                "INSERT INTO events (sport_id, home_team_id, away_team_id, event_time) VALUES (?, ?, ?, '2026-08-15T15:00:00Z')",
                (sport_id, home["id"], away["id"],)
            )
            db.commit()
            event_id = db.execute("SELECT id FROM events LIMIT 1").fetchone()["id"]

            cursor = db.execute("""
                INSERT INTO predictions (event_id, sport_id, market_type, market_name, selection,
                    estimated_probability, confidence, risk_score, data_quality)
                VALUES (?, ?, 'match_result', 'MR', 'home', 0.6, 70, 20, 40)
            """, (event_id, sport_id))
            db.commit()
            pred_id = cursor.lastrowid

            # Settle
            db.execute("""
                INSERT INTO prediction_results (prediction_id, outcome, actual_score_home, actual_score_away)
                VALUES (?, 'won', 2, 1)
            """, (pred_id,))
            db.execute("UPDATE predictions SET status = 'won', settled_at = datetime('now') WHERE id = ?", (pred_id,))
            db.commit()

            result = db.execute("SELECT * FROM prediction_results WHERE prediction_id = ?", (pred_id,)).fetchone()
            assert result["outcome"] == "won"
            assert result["actual_score_home"] == 2
            assert result["actual_score_away"] == 1


class TestIndexes:
    """Verify indexes are created for common query patterns."""

    def test_key_indexes_exist(self, fresh_db):
        with get_db() as db:
            indexes = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name"
            ).fetchall()
            names = {i["name"] for i in indexes}

        expected = [
            "idx_audit_created", "idx_audit_entity", "idx_events_sport",
            "idx_events_status", "idx_events_time", "idx_notifications_user",
            "idx_payments_transaction", "idx_payments_user", "idx_perf_period",
            "idx_predictions_confidence", "idx_predictions_event",
            "idx_predictions_sport", "idx_predictions_status",
            "idx_subscriptions_status", "idx_subscriptions_user",
        ]
        for name in expected:
            assert name in names, f"Index {name} missing"
