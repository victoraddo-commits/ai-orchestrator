"""Tests for DataIngestionManager._sync_sportsgameodds() — the main
supplemental ingestion path (events + odds + results + predictions) added
in the SportsGameOdds integration.

Complements test_settlement_fallback.py, which only covers the settlement
FALLBACK path (_sync_results_fallback_sgo(), used to settle PRIMARY-provider
events when the legacy scores provider is unconfigured/out of quota). This
file covers the independent, always-on supplemental sync: SGO's own events
get ingested, odds converted into qualified predictions, and SGO's own
finalized events get settled directly — all gated by its own quota/interval
and by dedup against events other providers already cover.
"""

from unittest.mock import patch

from core.kai_betting.data_ingestion import DataIngestionManager
from core.kai_betting.db import get_db, upsert_team, upsert_event
from core.kai_betting.prediction_engine import PredictionEngine


def _sgo_event(event_id="sgo-1", league_id="NBA", home="Golden State Warriors",
                away="Los Angeles Lakers", starts_at="2026-08-20T18:00:00Z",
                odds=None, finalized=False, home_pts=None, away_pts=None):
    evt = {
        "eventID": event_id,
        "leagueID": league_id,
        "teams": {
            "home": {"names": {"long": home}},
            "away": {"names": {"long": away}},
        },
        "status": {"startsAt": starts_at, "finalized": finalized},
    }
    if odds is not None:
        evt["odds"] = odds
    if finalized:
        evt["results"] = {"game": {"home": {"points": home_pts}, "away": {"points": away_pts}}}
    return evt


# oddID "points-home-game-ml-home" @ American -500 -> decimal 1.2, which
# clears every DEFAULT_QUALITY threshold (see prediction_engine.py).
_QUALIFYING_ODDS = {"points-home-game-ml-home": {"bookOdds": "-500"}}


def _event_row(db, sport_key, external_id):
    sport_id = db.execute("SELECT id FROM sports WHERE key = ?", (sport_key,)).fetchone()["id"]
    return db.execute(
        "SELECT e.*, ht.name home_name, at.name away_name FROM events e "
        "JOIN teams ht ON ht.id = e.home_team_id "
        "JOIN teams at ON at.id = e.away_team_id "
        "WHERE e.sport_id = ? AND e.external_id = ?",
        (sport_id, external_id),
    ).fetchone()


# ── Gating (skip conditions) ──────────────────────────────────────────────

def test_skipped_without_api_key(fresh_db, monkeypatch):
    monkeypatch.delenv("SPORTSGAMEODDS_API_KEY", raising=False)
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    result = mgr._sync_sportsgameodds()
    assert result == {"status": "skipped", "reason": "SPORTSGAMEODDS_API_KEY not set"}


def test_skipped_within_refresh_interval(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    with get_db() as db:
        mgr._mark_refreshed(db, "last_sportsgameodds_refresh")
        db.commit()

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
    ) as mock_fetch:
        result = mgr._sync_sportsgameodds()

    assert result["status"] == "skipped"
    assert result["reason"] == "within refresh interval"
    mock_fetch.assert_not_called()


def test_skipped_when_no_matching_leagues_for_active_sports(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO betting_config (key, value) VALUES "
            "('active_sports_for_sync', 'esports')"
        )
        db.commit()

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
    ) as mock_fetch:
        result = mgr._sync_sportsgameodds()

    assert result == {"status": "skipped", "reason": "no matching leagues for active sports"}
    mock_fetch.assert_not_called()


# ── Event ingestion ────────────────────────────────────────────────────────

def test_ingests_new_event_with_real_team_names(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    evt = _sgo_event()  # no odds -> no markets, no predictions

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["status"] == "ok"
    assert result["new_events"] == 1
    assert result["predictions_generated"] == 0

    with get_db() as db:
        row = _event_row(db, "basketball", "sgo-1")

    assert row["home_name"] == "Golden State Warriors"
    assert row["away_name"] == "Los Angeles Lakers"
    assert row["status"] == "scheduled"


def test_updates_existing_event_instead_of_duplicating(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    evt = _sgo_event()

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        first = mgr._sync_sportsgameodds()

        with get_db() as db:
            db.execute("DELETE FROM betting_config WHERE key = 'last_sportsgameodds_refresh'")
            db.commit()

        second = mgr._sync_sportsgameodds()

    assert first["new_events"] == 1
    assert second["new_events"] == 0
    assert second["updated_events"] == 1

    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) c FROM events WHERE external_id = 'sgo-1'"
        ).fetchone()["c"]
    assert count == 1


def test_unmapped_league_id_is_skipped_without_error(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    evt = _sgo_event(league_id="XFL")  # not in LEAGUE_ID_MAP / _EXTRA_LEAGUE_IDS

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["status"] == "ok"
    assert result["new_events"] == 0
    assert result["errors"] == 0

    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) c FROM events WHERE external_id = 'sgo-1'"
        ).fetchone()["c"]
    assert count == 0


# ── Dedup against other providers ─────────────────────────────────────────

def test_dedup_skips_event_matching_existing_primary_provider_event(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Golden State Warriors")
        away_id = upsert_team(db, sport_id, "Los Angeles Lakers")
        upsert_event(db, sport_id, "primary-ext-1", home_id, away_id,
                     "2026-08-20T18:30:00Z")  # within +/-3h of the SGO event below
        db.commit()

    evt = _sgo_event(event_id="sgo-dup-1", starts_at="2026-08-20T18:00:00Z")

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["status"] == "ok"
    assert result["dedup_skipped"] == 1
    assert result["new_events"] == 0

    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) c FROM events WHERE external_id = 'sgo-dup-1'"
        ).fetchone()["c"]
    assert count == 0


# ── Prediction generation ──────────────────────────────────────────────────

def test_generates_qualified_prediction_from_predictable_market(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    mgr._engine = PredictionEngine()

    evt = _sgo_event(odds=_QUALIFYING_ODDS)

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["predictions_generated"] == 1
    assert result["qualified_predictions"] == 1

    with get_db() as db:
        pred = db.execute("""
            SELECT p.market_type, p.selection, p.status FROM predictions p
            JOIN events e ON e.id = p.event_id
            WHERE e.external_id = 'sgo-1'
        """).fetchone()

    assert pred is not None
    assert pred["market_type"] == "match_result"
    assert pred["selection"] == "home"
    assert pred["status"] == "published"


def test_unpredictable_market_generates_no_prediction(fresh_db, monkeypatch):
    """extract_markets() only surfaces ml/ou (match_result/over_under); a
    market it can't classify (missing/garbled oddID) is simply absent from
    its output, so the predict_with_odds loop never runs for it."""
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    mgr._engine = PredictionEngine()

    evt = _sgo_event(odds={"garbled": {"bookOdds": "-500"}})

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["predictions_generated"] == 0
    assert result["qualified_predictions"] == 0


# ── Settlement of SGO's own finalized events ───────────────────────────────

def test_settles_finished_event_and_skips_prediction_generation(fresh_db, monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "fake-key")
    mgr = DataIngestionManager.__new__(DataIngestionManager)
    mgr._engine = PredictionEngine()

    with get_db() as db:
        sport_id = db.execute("SELECT id FROM sports WHERE key = 'basketball'").fetchone()["id"]
        home_id = upsert_team(db, sport_id, "Golden State Warriors")
        away_id = upsert_team(db, sport_id, "Los Angeles Lakers")
        event_id = upsert_event(db, sport_id, "sgo-finished-1", home_id, away_id,
                                "2026-08-10T18:00:00Z")
        db.execute("""
            INSERT INTO predictions (event_id, sport_id, market_type, market_name,
                selection, bookmaker_odds, estimated_probability, confidence,
                risk_score, data_quality, reasoning, status)
            VALUES (?, ?, 'match_result', 'Match Result', 'home', 1.2,
                0.84, 85.5, 10.0, 95.0, 'real reasoning', 'published')
        """, (event_id, sport_id))
        db.commit()

    evt = _sgo_event(
        event_id="sgo-finished-1", finalized=True, home_pts=101, away_pts=98,
        odds=_QUALIFYING_ODDS,  # present, but settlement must short-circuit before predicting
    )

    with patch(
        "core.kai_betting.data_sources.sportsgameodds.SportsGameOddsSource.fetch_events",
        return_value=[evt],
    ):
        result = mgr._sync_sportsgameodds()

    assert result["predictions_settled"] == 1
    assert result["predictions_generated"] == 0

    with get_db() as db:
        row = db.execute(
            "SELECT status, home_score, away_score FROM events WHERE external_id = 'sgo-finished-1'"
        ).fetchone()
        pred_row = db.execute(
            "SELECT status FROM predictions WHERE event_id = ?", (event_id,)
        ).fetchone()

    assert row["status"] == "finished"
    assert row["home_score"] == 101
    assert row["away_score"] == 98
    assert pred_row["status"] == "won"
