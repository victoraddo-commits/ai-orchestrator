"""Kai Betting — Data Ingestion Manager.

Orchestrates the pipeline from external sports data providers into
the Kai Betting database: fetch → transform → upsert.

Designed to be called from the worker cycle (every 300s), with
interval-gating so external API calls happen at sensible frequencies
rather than every cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Set

from core.kai_betting.db import (
    get_db, upsert_team, upsert_league, upsert_event,
)
from core.kai_betting.data_sources.odds_api import (
    OddsAPISource, SPORT_KEY_MAP, kai_sport_for,
    odds_sport_keys_for, _selection_from_outcome_name,
)
from core.kai_betting.prediction_engine import PredictionEngine

logger = logging.getLogger(__name__)


class DataIngestionManager:
    """Fetches sports data from configured providers and upserts into the DB.

    Uses the PredictionEngine to generate picks with real odds data.
    """

    def __init__(self, engine: Optional[PredictionEngine] = None):
        self._engine = engine or PredictionEngine()
        self._odds_api = OddsAPISource()

    @property
    def is_configured(self) -> bool:
        return self._odds_api.is_configured

    # ── Public entry points (called by workers) ────────────────────────────

    def refresh_events(self) -> Dict[str, Any]:
        """Fetch upcoming events and upsert into DB.

        Rate-limit gated: runs at most every events_interval_hours.
        Uses the free /events endpoint (no usage credits).
        """
        if not self.is_configured:
            return {"status": "skipped", "reason": "ODDS_API_KEY not set"}

        if not self._should_run("last_events_refresh",
                                self._config_int("events_interval_hours", 6) * 3600):
            return {"status": "skipped", "reason": "within refresh interval"}

        self._odds_api.refresh_rate_limits()

        active_sports = self._get_active_sports()
        if not active_sports:
            return {"status": "skipped", "reason": "no active sports configured"}

        # Collect Odds API sport keys for the active Kai sports
        odds_keys: List[str] = []
        for kai_sport in active_sports:
            odds_keys.extend(odds_sport_keys_for(kai_sport))

        if not odds_keys:
            return {"status": "skipped", "reason": "no Odds API sport keys for active sports"}

        total_new = 0
        total_updated = 0
        error_count = 0

        all_events = self._odds_api.fetch_events(odds_keys, days_ahead=7)

        with get_db() as db:
            for odds_key, events in all_events.items():
                kai_sport = kai_sport_for(odds_key)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                for evt in events:
                    try:
                        count = self._ingest_event(db, sport_id, odds_key, evt)
                        if count > 0:
                            total_new += 1
                        else:
                            total_updated += 1
                    except Exception as e:
                        logger.warning(f"Ingest failed for event {evt.get('id', '?')}: {e}")
                        error_count += 1

            self._mark_refreshed(db, "last_events_refresh")

        logger.info(
            f"refresh_events: {total_new} new, {total_updated} updated, "
            f"{error_count} errors, remaining_quota={self._odds_api.rate_limit_remaining}"
        )
        return {
            "status": "ok",
            "new_events": total_new,
            "updated_events": total_updated,
            "errors": error_count,
            "rate_limit_remaining": self._odds_api.rate_limit_remaining,
        }

    def refresh_sync(self, with_odds: bool = True,
                     with_results: bool = True) -> Dict[str, Any]:
        """Fetch odds and/or scores for existing events.

        Runs at intervals config-gated by last_odds_refresh / last_results_refresh.
        """
        if not self.is_configured:
            return {"status": "skipped", "reason": "ODDS_API_KEY not set"}

        self._odds_api.refresh_rate_limits()

        odds_result = {"status": "skipped", "reason": "odds sync disabled"}
        results_result = {"status": "skipped", "reason": "results sync disabled"}

        if with_odds and self._should_run("last_odds_refresh",
                                          self._config_int("odds_interval_minutes", 60) * 60):
            odds_result = self._sync_odds()

        if with_results and self._should_run("last_results_refresh",
                                             self._config_int("results_interval_minutes", 15) * 60):
            try:
                results_result = self._sync_results()
            except Exception as e:
                import traceback as _tb
                logger.error(f"_sync_results failed: {e}", exc_info=True)
                results_result = {"status": "error", "error": str(e)}

        return {
            "odds": odds_result,
            "results": results_result,
            "rate_limit_remaining": self._odds_api.rate_limit_remaining,
        }

    # ── Ingestion helpers ──────────────────────────────────────────────────

    def _ingest_event(self, db, sport_id: int, odds_key: str,
                      evt: Dict[str, Any]) -> int:
        """Upsert a single event record.  Returns 1 if new, 0 if updated."""
        external_id = str(evt.get("id", ""))
        if not external_id:
            return 0

        home_name = evt.get("home_team", "Home")
        away_name = evt.get("away_team", "Away")
        event_time = evt.get("commence_time", "")

        # Upsert teams
        home_id = upsert_team(db, sport_id, home_name)
        away_id = upsert_team(db, sport_id, away_name)

        # Try to extract a league from the odds sport key (e.g. "soccer_epl" → "epl")
        league_key = odds_key.split("_", 1)[-1] if "_" in odds_key else odds_key
        league_name = league_key.replace("_", " ").title()

        # Check if event already exists (to distinguish new vs updated)
        existing = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, external_id)
        ).fetchone()

        league_id = upsert_league(db, sport_id, league_key, league_name)

        upsert_event(
            db, sport_id, external_id,
            home_id, away_id, event_time,
            league_id=league_id,
            status="scheduled",
        )

        return 1 if existing is None else 0

    def _sync_odds(self) -> Dict[str, Any]:
        """Fetch odds for scheduled events and generate predictions."""
        if not self._odds_api.has_quota():
            return {"status": "skipped", "reason": "rate limit exhausted"}

        active_sports = self._get_active_sports()
        odds_keys: List[str] = []
        for kai_sport in active_sports:
            odds_keys.extend(odds_sport_keys_for(kai_sport))

        if not odds_keys:
            return {"status": "skipped", "reason": "no active sports"}

        all_odds = self._odds_api.fetch_odds(odds_keys)

        total_predictions = 0
        total_events = 0
        error_count = 0

        with get_db() as db:
            for odds_key, odds_events in all_odds.items():
                kai_sport = kai_sport_for(odds_key)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                for odds_evt in odds_events:
                    if not isinstance(odds_evt, dict):
                        logger.warning(
                            f"sync_odds: unexpected event type "
                            f"{type(odds_evt).__name__} for {odds_key}: {odds_evt!r}"
                        )
                        continue
                    total_events += 1
                    ext_id = str(odds_evt.get("id", ""))
                    home_team = odds_evt.get("home_team", "")
                    away_team = odds_evt.get("away_team", "")

                    # Find the Kai event
                    event_row = db.execute(
                        "SELECT id, home_team_id, away_team_id FROM events WHERE sport_id = ? AND external_id = ?",
                        (sport_id, ext_id)
                    ).fetchone()
                    event_id = event_row["id"] if event_row else None

                    # Iterate bookmakers and markets
                    for bookmaker in odds_evt.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            market_key = market.get("key", "")
                            kai_market = MARKET_MAP_INTERNAL.get(market_key)
                            if not kai_market:
                                continue

                            for outcome in market.get("outcomes", []):
                                price = outcome.get("price")
                                if not price or price <= 1.0:
                                    continue

                                name = outcome.get("name", "")
                                selection = _selection_from_outcome_name(
                                    name, kai_market, home_team, away_team
                                )

                                # For over_under, embed the line from the market point
                                if kai_market == "over_under":
                                    point = market.get("point")
                                    if point:
                                        kai_market = f"over_under_{point}"

                                try:
                                    result = self._engine.predict(
                                        sport_key=kai_sport,
                                        market_type=kai_market.split("_", 2)[0]
                                        if kai_market.startswith("over_under_")
                                        else kai_market,
                                        home_team=home_team,
                                        away_team=away_team,
                                        bookmaker_odds=float(price),
                                    )

                                    # Determine publish status
                                    status = self._determine_publish_status(db, result)

                                    # Upsert prediction (dedup by event + market + selection)
                                    self._upsert_prediction(
                                        db, event_id, sport_id, result, status
                                    )
                                    total_predictions += 1

                                except Exception as e:
                                    logger.warning(
                                        f"Prediction failed for {odds_key}/{ext_id}/{name}: {e}"
                                    )
                                    error_count += 1

            self._mark_refreshed(db, "last_odds_refresh")

        logger.info(
            f"sync_odds: {total_predictions} predictions from {total_events} events, "
            f"{error_count} errors"
        )
        return {
            "status": "ok",
            "predictions_generated": total_predictions,
            "events_processed": total_events,
            "errors": error_count,
        }

    def _sync_results(self) -> Dict[str, Any]:
        """Fetch scores for recent/live events and update DB."""
        if not self._odds_api.has_quota():
            return {"status": "skipped", "reason": "rate limit exhausted"}

        active_sports = self._get_active_sports()
        odds_keys: List[str] = []
        for kai_sport in active_sports:
            odds_keys.extend(odds_sport_keys_for(kai_sport))

        if not odds_keys:
            return {"status": "skipped", "reason": "no active sports"}

        all_scores = self._odds_api.fetch_scores(odds_keys, days_from=3)

        total_updated = 0
        total_settled = 0

        with get_db() as db:
            for odds_key, score_events in all_scores.items():
                kai_sport = kai_sport_for(odds_key)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                for se in score_events:
                    if not isinstance(se, dict):
                        logger.warning(
                            f"sync_results: unexpected score event type "
                            f"{type(se).__name__} for {odds_key}: {se!r}"
                        )
                        continue
                    if not se.get("completed", False):
                        continue

                    ext_id = str(se.get("id", ""))
                    raw_scores = se.get("scores")
                    # The Odds API returns scores as either a dict
                    # {home_score, away_score} or a list [{name, score}].
                    if isinstance(raw_scores, dict):
                        home_score = raw_scores.get("home_score")
                        away_score = raw_scores.get("away_score")
                    elif isinstance(raw_scores, list) and raw_scores:
                        # List format: extract numeric scores
                        home_score = None
                        away_score = None
                        home_team = se.get("home_team", "")
                        away_team = se.get("away_team", "")
                        for item in raw_scores:
                            if not isinstance(item, dict):
                                continue
                            item_name = item.get("name", "")
                            item_score = item.get("score")
                            if item_name == home_team:
                                home_score = item_score
                            elif item_name == away_team:
                                away_score = item_score
                    else:
                        home_score = None
                        away_score = None

                    # Normalize to int for DB INTEGER column
                    try:
                        home_score = int(home_score) if home_score is not None else None
                    except (ValueError, TypeError):
                        home_score = None
                    try:
                        away_score = int(away_score) if away_score is not None else None
                    except (ValueError, TypeError):
                        away_score = None

                    if home_score is None and away_score is None:
                        continue

                    # Update event status and scores
                    cursor = db.execute("""
                        UPDATE events SET status = 'finished',
                        home_score = ?, away_score = ?, updated_at = datetime('now')
                        WHERE sport_id = ? AND external_id = ?
                    """, (home_score, away_score, sport_id, ext_id))
                    if cursor.rowcount > 0:
                        total_updated += 1

                    # Trigger auto-settlement for this event's predictions
                    settled = self._settle_event_predictions(
                        db, sport_id, ext_id, home_score, away_score
                    )
                    total_settled += settled

            self._mark_refreshed(db, "last_results_refresh")

        logger.info(
            f"sync_results: {total_updated} events updated, {total_settled} predictions settled"
        )
        return {
            "status": "ok",
            "events_updated": total_updated,
            "predictions_settled": total_settled,
        }

    # ── Config helpers ─────────────────────────────────────────────────────

    def _should_run(self, config_key: str, interval_seconds: int) -> bool:
        """Check if enough time has passed since the last run."""
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = ?", (config_key,)
            ).fetchone()
            if not row or not row["value"]:
                return True  # never run before

            try:
                last = datetime.fromisoformat(row["value"])
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                return elapsed >= interval_seconds
            except (ValueError, OSError):
                return True

    def _mark_refreshed(self, db, config_key: str):
        """Update the last-refreshed timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO betting_config (key, value) VALUES (?, ?)",
            (config_key, now),
        )

    def _config_int(self, key: str, default: int) -> int:
        """Read an integer from betting_config."""
        try:
            with get_db() as db:
                row = db.execute(
                    "SELECT value FROM betting_config WHERE key = ?", (key,)
                ).fetchone()
                if row and row["value"]:
                    return int(row["value"])
        except Exception:
            pass
        return default

    def _get_active_sports(self) -> List[str]:
        """Return the list of Kai sport keys to sync, from betting_config."""
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = 'active_sports_for_sync'"
            ).fetchone()
            if row and row["value"]:
                return [s.strip() for s in row["value"].split(",") if s.strip()]
        return ["football", "basketball", "tennis"]

    def _determine_publish_status(self, db, result) -> str:
        """Decide whether a prediction should be auto-published."""
        auto_pub_row = db.execute(
            "SELECT value FROM betting_config WHERE key = 'auto_publish'"
        ).fetchone()
        should_publish = auto_pub_row and auto_pub_row["value"] == "true"

        if not should_publish:
            return "pending"

        min_conf = float(db.execute(
            "SELECT value FROM betting_config WHERE key = 'min_confidence_publish'"
        ).fetchone()["value"])
        min_edge = float(db.execute(
            "SELECT value FROM betting_config WHERE key = 'min_edge_publish'"
        ).fetchone()["value"])

        if result.confidence >= min_conf and (result.edge or 0) >= min_edge:
            return "published"
        return "pending"

    def _upsert_prediction(self, db, event_id, sport_id, result, status):
        """Insert or update a prediction in the DB."""
        if event_id is None:
            return

        # Check for existing prediction with same event+market+selection
        existing = db.execute("""
            SELECT id FROM predictions
            WHERE event_id = ? AND market_type = ? AND selection = ?
              AND bookmaker_odds IS NOT NULL
            LIMIT 1
        """, (event_id, result.market_type, result.selection)).fetchone()

        if existing:
            # Update existing
            db.execute("""
                UPDATE predictions SET
                    bookmaker_odds = ?, estimated_probability = ?,
                    implied_probability = ?, edge = ?,
                    confidence = ?, risk_score = ?, data_quality = ?,
                    reasoning = ?, tags = ?, status = ?,
                    data_timestamp = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
            """, (
                result.bookmaker_odds, result.estimated_probability,
                result.implied_probability, result.edge,
                result.confidence, result.risk_score, result.data_quality,
                result.reasoning, ",".join(result.tags), status,
                existing["id"],
            ))
        else:
            # Insert new
            db.execute("""
                INSERT INTO predictions (
                    event_id, sport_id, market_type, market_name, selection,
                    bookmaker_odds, estimated_probability, implied_probability, edge,
                    confidence, risk_score, data_quality, reasoning, tags,
                    correlation_group, model_version, status, data_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                event_id, sport_id,
                result.market_type, result.market_name, result.selection,
                result.bookmaker_odds, result.estimated_probability,
                result.implied_probability, result.edge,
                result.confidence, result.risk_score, result.data_quality,
                result.reasoning, ",".join(result.tags),
                result.correlation_group, result.model_version, status,
            ))

    def _settle_event_predictions(self, db, sport_id, ext_id,
                                  home_score, away_score) -> int:
        """Settle predictions for a finished event. Returns count settled."""
        from core.kai_betting.workers import KaiBettingWorkers

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, ext_id)
        ).fetchone()
        if not event_row:
            return 0
        event_id = event_row["id"]

        rows = db.execute("""
            SELECT p.id, p.selection, p.market_type
            FROM predictions p
            WHERE p.event_id = ? AND p.status IN ('pending','published','quality_check','approved')
        """, (event_id,)).fetchall()

        settled = 0
        for row in rows:
            outcome = KaiBettingWorkers._determine_outcome(
                row["market_type"], row["selection"],
                home_score or 0, away_score or 0,
            )
            if outcome:
                db.execute("""
                    INSERT OR REPLACE INTO prediction_results
                    (prediction_id, outcome, actual_score_home, actual_score_away, settled_by)
                    VALUES (?, ?, ?, ?, 'auto')
                """, (row["id"], outcome, home_score, away_score))
                db.execute("""
                    UPDATE predictions SET status = ?, settled_at = datetime('now'), updated_at = datetime('now')
                    WHERE id = ?
                """, (outcome, row["id"]))
                settled += 1

        return settled


# Local market mapping (same as odds_api but used internally)
MARKET_MAP_INTERNAL = {
    "h2h": "match_result",
    "totals": "over_under",
    "spreads": "handicap",
}
