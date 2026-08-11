"""Kai Betting — Database Schema and Operations.

All tables use normalized design with proper indexes.
Uses the same SQLite pattern as the rest of Kai infrastructure.
"""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

DB_PATH = os.environ.get("KAI_BETTING_DB", os.path.join(
    os.path.dirname(__file__), "..", "..", "memory", "kai_betting.db"
))

SCHEMA_VERSION = 1

# ── Complete Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Users & Authentication
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    password_hash TEXT,
    full_name TEXT DEFAULT '',
    country TEXT DEFAULT '',
    phone_number TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telegram_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_id TEXT UNIQUE NOT NULL,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    chat_id TEXT NOT NULL,
    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1
);

-- Sports & Leagues
CREATE TABLE IF NOT EXISTS sports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,        -- 'football', 'basketball', etc.
    name TEXT NOT NULL,               -- 'Football', 'Basketball'
    icon TEXT DEFAULT '',             -- emoji or icon name
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport_id INTEGER NOT NULL REFERENCES sports(id) ON DELETE CASCADE,
    key TEXT NOT NULL,                -- 'english-premier-league'
    name TEXT NOT NULL,               -- 'English Premier League'
    country TEXT DEFAULT '',
    tier INTEGER DEFAULT 1,          -- 1 = top division, 2 = second, etc.
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(sport_id, key)
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport_id INTEGER NOT NULL REFERENCES sports(id) ON DELETE CASCADE,
    league_id INTEGER REFERENCES leagues(id) ON DELETE SET NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT DEFAULT '',
    country TEXT DEFAULT '',
    external_id TEXT DEFAULT '',         -- data provider's team ID
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(sport_id, key)
);

-- Events (matches/games)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT,                 -- data provider's ID
    sport_id INTEGER NOT NULL REFERENCES sports(id),
    league_id INTEGER REFERENCES leagues(id),
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    event_time TEXT NOT NULL,         -- ISO 8601
    status TEXT NOT NULL DEFAULT 'scheduled',
        -- 'scheduled', 'live', 'finished', 'postponed', 'cancelled', 'abandoned'
    home_score INTEGER,
    away_score INTEGER,
    venue TEXT DEFAULT '',
    round TEXT DEFAULT '',            -- 'Matchday 1', 'Quarter Finals', etc.
    season TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_sport ON events(sport_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(sport_id, external_id) WHERE external_id IS NOT NULL AND external_id != '';

-- Event statistics
CREATE TABLE IF NOT EXISTS event_statistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    stat_type TEXT NOT NULL,          -- 'recent_form_home', 'h2h_wins', 'injuries', etc.
    stat_key TEXT NOT NULL,
    stat_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,      -- data quality confidence 0-1
    source TEXT DEFAULT '',
    data_timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, stat_type, stat_key)
);

-- Prediction Models
CREATE TABLE IF NOT EXISTS prediction_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 'statistical-v1', 'ml-ensemble-v2', etc.
    model_type TEXT NOT NULL,         -- 'statistical', 'ml', 'ai', 'ensemble'
    version TEXT NOT NULL DEFAULT '1.0.0',
    provider TEXT DEFAULT '',         -- AI provider used
    config TEXT DEFAULT '{}',         -- JSON config
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Predictions
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    model_id INTEGER REFERENCES prediction_models(id),
    sport_id INTEGER NOT NULL REFERENCES sports(id),
    league_id INTEGER REFERENCES leagues(id),

    -- Market info
    market_type TEXT NOT NULL,        -- 'match_result', 'double_chance', 'over_under', etc.
    market_name TEXT NOT NULL,        -- 'Double Chance — 1X', 'Over 2.5 Goals'
    selection TEXT NOT NULL,          -- 'home', 'draw', 'away', 'over', 'under', 'yes', 'no'

    -- Odds & Probability
    bookmaker_odds REAL,              -- odds from bookmaker
    estimated_probability REAL NOT NULL, -- model's estimated probability 0-1
    implied_probability REAL,         -- 1 / bookmaker_odds
    edge REAL,                        -- estimated_probability - implied_probability

    -- Confidence & Risk
    confidence REAL NOT NULL,         -- 0-100 confidence score
    risk_score REAL NOT NULL DEFAULT 0.0,  -- 0-100 risk score
    data_quality REAL NOT NULL DEFAULT 0.0, -- 0-100 data quality score

    -- Metadata
    reasoning TEXT DEFAULT '',
    tags TEXT DEFAULT '',             -- comma-separated tags
    model_version TEXT DEFAULT '',
    data_timestamp TEXT,
    correlation_group TEXT DEFAULT '', -- shared key for correlated picks

    -- Status
    status TEXT NOT NULL DEFAULT 'pending',
        -- 'pending', 'quality_check', 'approved', 'rejected',
        -- 'published', 'won', 'lost', 'push', 'void', 'cancelled'
    published_at TEXT,
    settled_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_predictions_event ON predictions(event_id);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_predictions_sport ON predictions(sport_id);
CREATE INDEX IF NOT EXISTS idx_predictions_confidence ON predictions(confidence);

-- Prediction results / settlement
CREATE TABLE IF NOT EXISTS prediction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL UNIQUE REFERENCES predictions(id),
    outcome TEXT NOT NULL,            -- 'won', 'lost', 'push', 'void'
    actual_score_home INTEGER,
    actual_score_away INTEGER,
    settled_by TEXT DEFAULT 'auto',  -- 'auto', 'manual'
    settled_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT DEFAULT ''
);

-- Odds Groups
CREATE TABLE IF NOT EXISTS odds_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_odds REAL NOT NULL,        -- 5, 10, 50, 100, 200, 500, 1000
    label TEXT NOT NULL,              -- '5 ODDS', '10 ODDS', etc.
    risk_level TEXT NOT NULL,         -- 'conservative', 'moderate', 'aggressive', etc.
    combined_odds REAL NOT NULL,
    estimated_probability REAL,
    average_confidence REAL NOT NULL,
    num_selections INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    published_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS odds_group_selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    odds_group_id INTEGER NOT NULL REFERENCES odds_groups(id) ON DELETE CASCADE,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    sort_order INTEGER DEFAULT 0,
    UNIQUE(odds_group_id, prediction_id)
);

-- Subscriptions & Plans
CREATE TABLE IF NOT EXISTS subscription_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,         -- 'daily', 'weekly', 'monthly', 'yearly'
    name TEXT NOT NULL,               -- 'Daily Access', 'Weekly Premium', etc.
    duration_days INTEGER NOT NULL,
    price REAL NOT NULL,              -- in base currency (GHS)
    currency TEXT NOT NULL DEFAULT 'GHS',
    features TEXT DEFAULT '{}',       -- JSON: {"premium_sports": [...], "max_picks": 10}
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
    status TEXT NOT NULL DEFAULT 'pending',
        -- 'pending', 'active', 'expired', 'cancelled', 'refunded'
    started_at TEXT,
    expires_at TEXT,
    auto_renew INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

-- Payments
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    subscription_id INTEGER REFERENCES subscriptions(id),
    transaction_id TEXT UNIQUE,       -- external payment reference
    provider TEXT NOT NULL,           -- 'hubtel', 'stripe', etc.
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'GHS',
    status TEXT NOT NULL DEFAULT 'pending',
        -- 'pending', 'processing', 'completed', 'failed', 'refunded'
    payment_method TEXT DEFAULT '',   -- 'mobile_money', 'card', 'crypto'
    provider_response TEXT DEFAULT '{}', -- JSON
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_transaction ON payments(transaction_id);

-- User Preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    selected_sports TEXT DEFAULT '',   -- comma-separated sport keys
    selected_markets TEXT DEFAULT '',  -- comma-separated market types
    notification_picks INTEGER NOT NULL DEFAULT 1,
    notification_results INTEGER NOT NULL DEFAULT 1,
    notification_odds_groups INTEGER NOT NULL DEFAULT 0,
    notification_daily_summary INTEGER NOT NULL DEFAULT 0,
    telegram_notifications INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,               -- 'pick', 'result', 'odds_group', 'subscription', 'system'
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',  -- 'telegram', 'email', 'web'
    is_read INTEGER NOT NULL DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

-- Data Sources
CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 'api-football', 'odds-api', etc.
    provider TEXT NOT NULL,
    endpoint TEXT DEFAULT '',
    api_status TEXT DEFAULT 'unknown', -- 'healthy', 'degraded', 'down'
    last_check_at TEXT,
    rate_limit_remaining INTEGER,
    rate_limit_total INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Audit Logs
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    user_id INTEGER,
    details TEXT DEFAULT '{}',
    ip_address TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

-- Admin Config (feature flags, thresholds)
CREATE TABLE IF NOT EXISTS betting_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Performance Tracking
CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,             -- 'daily', 'weekly', 'monthly', 'all_time'
    period_start TEXT NOT NULL,
    period_end TEXT,
    sport_id INTEGER REFERENCES sports(id),
    market_type TEXT,
    total_predictions INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    pushes INTEGER NOT NULL DEFAULT 0,
    voids INTEGER NOT NULL DEFAULT 0,
    win_rate REAL DEFAULT 0.0,
    roi REAL DEFAULT 0.0,
    average_odds REAL DEFAULT 0.0,
    average_confidence REAL DEFAULT 0.0,
    profit_loss REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_perf_period ON performance_metrics(period, period_start);
"""

# ── Seed Data ────────────────────────────────────────────────────────────────

SPORT_SEEDS = [
    ("football", "⚽ Football", 1),
    ("basketball", "🏀 Basketball", 2),
    ("tennis", "🎾 Tennis", 3),
    ("baseball", "⚾ Baseball", 4),
    ("ice_hockey", "🏒 Ice Hockey", 5),
    ("american_football", "🏈 American Football", 6),
    ("rugby", "🏉 Rugby", 7),
    ("volleyball", "🏐 Volleyball", 8),
    ("handball", "🤾 Handball", 9),
    ("cricket", "🏏 Cricket", 10),
]

MARKET_SEEDS = [
    # Football
    ("football", "match_result", "Match Result", "1X2"),
    ("football", "double_chance", "Double Chance", "1X, X2, 12"),
    ("football", "draw_no_bet", "Draw No Bet", "Home, Away"),
    ("football", "over_under", "Over/Under Goals", "Over/Under 0.5-3.5"),
    ("football", "btts", "Both Teams To Score", "Yes/No"),
    ("football", "team_goals", "Team Goals", "Home/Away Over/Under"),
    ("football", "ht_result", "Half-Time Result", "1X2"),
    ("football", "ht_goals", "Half-Time Goals", "Over/Under"),
    # Basketball
    ("basketball", "match_result", "Match Result", "1X2"),
    ("basketball", "over_under", "Over/Under Points", "Total Points"),
    ("basketball", "handicap", "Handicap", "Spread"),
    # Tennis
    ("tennis", "match_result", "Match Result", "Player Win"),
    ("tennis", "set_betting", "Set Betting", "Set Score"),
    ("tennis", "over_under", "Over/Under Games", "Total Games"),
    # Baseball
    ("baseball", "match_result", "Match Result", "Moneyline"),
    ("baseball", "over_under", "Over/Under Runs", "Total Runs"),
    # Common to multiple sports
    ("ice_hockey", "match_result", "Match Result", "1X2"),
    ("ice_hockey", "over_under", "Over/Under Goals", "Total Goals"),
    ("american_football", "match_result", "Match Result", "Moneyline"),
    ("american_football", "over_under", "Over/Under Points", "Total Points"),
]

DEFAULT_SUBSCRIPTION_PLANS = [
    ("daily", "Daily Access", 1, 5.0, "GHS", '{"premium_sports": ["all"], "max_picks": 10}'),
    ("weekly", "Weekly Premium", 7, 25.0, "GHS", '{"premium_sports": ["all"], "max_picks": 50}'),
    ("monthly", "Monthly Premium", 30, 80.0, "GHS", '{"premium_sports": ["all"], "max_picks": -1}'),
]

DEFAULT_CONFIG = {
    "min_confidence_publish": "50",
    "min_data_quality_publish": "30",
    "min_edge_publish": "0.02",
    "max_odds_group_selections": "50",
    "daily_publish_time": "08:00",
    "free_daily_picks": "3",
    "premium_daily_picks": "20",
    "auto_publish": "false",
    "auto_settle": "true",
    "correlation_threshold": "0.6",
    "backtest_min_samples": "100",
    "feature_live_predictions": "false",
    # Data source refresh tracking
    "last_events_refresh": "",
    "last_odds_refresh": "",
    "last_results_refresh": "",
    "last_rate_limit_reset": "",
    "active_sports_for_sync": "football,basketball,tennis",
}


# ── Connection Management ───────────────────────────────────────────────────

def get_db_path() -> str:
    """Ensure the directory exists and return the DB path."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the Kai Betting database schema and seed data."""
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)

        # Check schema version
        version_row = conn.execute(
            "SELECT value FROM betting_config WHERE key = 'schema_version'"
        ).fetchone()

        if version_row is None:
            # Fresh install — seed data
            _seed_data(conn)
            conn.execute(
                "INSERT OR REPLACE INTO betting_config (key, value, description) "
                "VALUES ('schema_version', ?, 'Database schema version')",
                (str(SCHEMA_VERSION),)
            )

        # Always ensure default config exists
        for key, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO betting_config (key, value) VALUES (?, ?)",
                (key, value)
            )

        conn.commit()
    return True


# ── Data Ingestion Helpers ──────────────────────────────────────────────────


def upsert_team(conn, sport_id: int, name: str, short_name: str = "",
                country: str = "", external_id: str = "") -> int:
    """Insert or get a team, returning its ID.

    Generates a stable key from the team name by slugifying.
    Uses INSERT OR IGNORE so duplicate key violations safely return
    the existing row.
    """
    import re
    key = re.sub(r'[^a-z0-9]+', '_', name.lower().strip()).strip('_')
    if not key:
        key = f"team_{sport_id}_{hash(name) & 0xFFFF:04x}"

    conn.execute("""
        INSERT OR IGNORE INTO teams (sport_id, key, name, short_name, country, external_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (sport_id, key, name, short_name, country, external_id))

    row = conn.execute(
        "SELECT id FROM teams WHERE sport_id = ? AND key = ?",
        (sport_id, key)
    ).fetchone()
    return row["id"]


def upsert_league(conn, sport_id: int, league_key: str, name: str,
                  country: str = "", tier: int = 1) -> int:
    """Insert or get a league, returning its ID."""
    conn.execute("""
        INSERT OR IGNORE INTO leagues (sport_id, key, name, country, tier)
        VALUES (?, ?, ?, ?, ?)
    """, (sport_id, league_key, name, country, tier))

    row = conn.execute(
        "SELECT id FROM leagues WHERE sport_id = ? AND key = ?",
        (sport_id, league_key)
    ).fetchone()
    return row["id"]


def upsert_event(conn, sport_id: int, external_id: str,
                 home_team_id: int, away_team_id: int,
                 event_time: str, league_id: int = None,
                 status: str = "scheduled", venue: str = "",
                 round_name: str = "", season: str = "") -> int:
    """Insert or update an event, returning its ID.

    Uses UPDATE-or-INSERT (not INSERT OR REPLACE) to avoid
    FOREIGN KEY violations when predictions already reference
    the event.  (REPLACE = DELETE + INSERT; DELETE fails if
    child rows exist and the FK lacks ON DELETE CASCADE.)
    """
    existing = conn.execute(
        "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
        (sport_id, external_id)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE events SET
                home_team_id = ?, away_team_id = ?,
                league_id = ?, event_time = ?,
                status = ?, venue = ?, round = ?, season = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (home_team_id, away_team_id, league_id, event_time,
              status, venue, round_name, season, existing["id"]))
        return existing["id"]

    conn.execute("""
        INSERT INTO events (
            external_id, sport_id, league_id,
            home_team_id, away_team_id, event_time,
            status, venue, round, season, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (external_id, sport_id, league_id,
          home_team_id, away_team_id, event_time,
          status, venue, round_name, season))

    row = conn.execute(
        "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
        (sport_id, external_id)
    ).fetchone()
    return row["id"]


def _seed_data(conn):
    """Insert initial seed data."""
    # Sports
    for key, name, sort_order in SPORT_SEEDS:
        conn.execute(
            "INSERT OR IGNORE INTO sports (key, name, sort_order) VALUES (?, ?, ?)",
            (key, name, sort_order)
        )

    # Subscription plans
    for key, name, days, price, currency, features in DEFAULT_SUBSCRIPTION_PLANS:
        conn.execute(
            "INSERT OR IGNORE INTO subscription_plans "
            "(key, name, duration_days, price, currency, features) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, name, days, price, currency, features)
        )

    # Default prediction model
    conn.execute(
        "INSERT OR IGNORE INTO prediction_models (name, model_type, version) "
        "VALUES ('kai-betting-v1', 'ensemble', '1.0.0')"
    )
