"""Kai Betting — Automation Workers (Phase 16).

Scheduled tasks that run on the Kai orchestrator cycle:
  - Daily prediction generation from event data
  - Auto-settlement of finished matches
  - Subscription expiry checks
  - Odds group refresh
  - Performance metrics computation
  - Data source health checks
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from core.kai_betting.db import get_db, init_db
from core.kai_betting.prediction_engine import PredictionEngine
from core.kai_betting.odds_engine import OddsEngine
from core.kai_betting.subscriptions import SubscriptionManager
from core.kai_betting.performance import PerformanceTracker
from core.kai_betting.api import _ensure_prediction_saved
from core.kai_betting.data_ingestion import DataIngestionManager

logger = logging.getLogger(__name__)


class KaiBettingWorkers:
    """Collection of scheduled automation tasks.

    Invoked from the Kai scheduler loop. Each worker is idempotent
    and safe to run multiple times per cycle.
    """

    def __init__(self):
        self._prediction_engine = PredictionEngine()
        self._subscription_mgr = SubscriptionManager()
        self._perf_tracker = PerformanceTracker()
        self._ingestion = DataIngestionManager(engine=self._prediction_engine)

    # ── Data Ingestion (real odds from external providers) ──────────────────

    def refresh_events(self) -> Dict[str, Any]:
        """Fetch upcoming events from data providers and upsert into DB.

        Delegates to DataIngestionManager which handles rate limiting,
        interval gating, and provider-specific logic.
        """
        return self._ingestion.refresh_events()

    def refresh_sync(self, with_odds: bool = True,
                     with_results: bool = True) -> Dict[str, Any]:
        """Fetch odds and/or scores for existing events.

        Delegates to DataIngestionManager which handles rate limiting,
        interval gating, and provider-specific logic.
        """
        return self._ingestion.refresh_sync(with_odds=with_odds, with_results=with_results)

    # ── Daily Prediction Generation ──────────────────────────────────────────

    def generate_daily_predictions(self, sport_keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate predictions for today's scheduled events.

        Queries events happening today and runs the prediction engine
        for each event against relevant markets.

        Args:
            sport_keys: Optional list of sport keys to generate for.
                       If None, generates for all active sports.
        """
        with get_db() as db:
            # Get config
            auto_publish = db.execute(
                "SELECT value FROM betting_config WHERE key = 'auto_publish'"
            ).fetchone()
            should_publish = auto_publish and auto_publish["value"] == "true"
            min_conf = float((db.execute(
                "SELECT value FROM betting_config WHERE key = 'min_confidence_publish'"
            ).fetchone() or {"value": "50"})["value"])
            min_edge = float((db.execute(
                "SELECT value FROM betting_config WHERE key = 'min_edge_publish'"
            ).fetchone() or {"value": "0.02"})["value"])

            # Get today's events
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            query = """
                SELECT e.*, s.key as sport_key, ht.name as home_team, at.name as away_team
                FROM events e
                JOIN sports s ON s.id = e.sport_id
                JOIN teams ht ON ht.id = e.home_team_id
                JOIN teams at ON at.id = e.away_team_id
                WHERE date(e.event_time) = date(?)
                  AND e.status = 'scheduled'
            """
            params = [today]
            if sport_keys:
                placeholders = ",".join(["?"] * len(sport_keys))
                query += f" AND s.key IN ({placeholders})"
                params.extend(sport_keys)

            events = db.execute(query, params).fetchall()

        generated = 0
        published = 0
        errors = 0

        for event in events:
            try:
                # Skip events that already have predictions with real bookmaker odds
                # (they were already covered by refresh_sync)
                existing_odds = db.execute(
                    "SELECT COUNT(*) as cnt FROM predictions WHERE event_id = ? AND bookmaker_odds IS NOT NULL",
                    (event["id"],)
                ).fetchone()
                if existing_odds and existing_odds["cnt"] > 0:
                    continue  # Already has real-odds predictions — skip synthetic generation

                sport_key = event["sport_key"]
                # Get available markets for this sport from config
                markets = self._get_markets_for_sport(db, sport_key)

                for market_type in markets:
                    result = self._prediction_engine.predict(
                        sport_key=sport_key,
                        market_type=market_type,
                        home_team=event["home_team"],
                        away_team=event["away_team"],
                    )

                    # Determine status
                    status = "pending"
                    if should_publish and result.confidence >= min_conf:
                        if result.edge and result.edge >= min_edge:
                            status = "published"

                    # Save
                    with get_db() as conn:
                        conn.execute("""
                            INSERT INTO predictions (
                                event_id, sport_id, league_id,
                                market_type, market_name, selection,
                                bookmaker_odds, estimated_probability,
                                implied_probability, edge,
                                confidence, risk_score, data_quality,
                                reasoning, tags, correlation_group,
                                model_version, status, data_timestamp
                            ) VALUES (?, (SELECT id FROM sports WHERE key = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """, (
                            event["id"],
                            sport_key,
                            event["league_id"],
                            market_type,
                            result.market_name,
                            result.selection,
                            result.bookmaker_odds,
                            result.estimated_probability,
                            result.implied_probability,
                            result.edge,
                            result.confidence,
                            result.risk_score,
                            result.data_quality,
                            result.reasoning,
                            ",".join(result.tags),
                            result.correlation_group,
                            result.model_version,
                            status,
                        ))
                        conn.commit()

                    generated += 1
                    if status == "published":
                        published += 1

            except Exception as e:
                logger.error(f"Prediction generation failed for event {event.get('id')}: {e}")
                errors += 1

        logger.info(
            f"Daily predictions: {generated} generated, {published} auto-published, {errors} errors"
        )

        return {
            "generated": generated,
            "published": published,
            "errors": errors,
            "date": today,
        }

    # ── Auto-Settlement ──────────────────────────────────────────────────────

    def auto_settle_finished(self) -> Dict[str, Any]:
        """Auto-settle predictions for finished events.

        Checks events with status 'finished' and settles any pending
        predictions against those events.
        """
        with get_db() as db:
            auto_settle = db.execute(
                "SELECT value FROM betting_config WHERE key = 'auto_settle'"
            ).fetchone()
            if not auto_settle or auto_settle["value"] != "true":
                return {"settled": 0, "reason": "auto_settle disabled"}

            # Find finished events with pending predictions
            rows = db.execute("""
                SELECT p.id as prediction_id, p.selection, p.market_type,
                       e.id as event_id, e.home_score, e.away_score,
                       e.status as event_status
                FROM predictions p
                JOIN events e ON e.id = p.event_id
                WHERE p.status IN ('pending', 'published', 'quality_check', 'approved')
                  AND e.status = 'finished'
                  AND e.home_score IS NOT NULL
                  AND e.away_score IS NOT NULL
            """).fetchall()

        settled = 0
        for row in rows:
            outcome = self._determine_outcome(
                row["market_type"],
                row["selection"],
                row["home_score"],
                row["away_score"],
            )

            if outcome:
                with get_db() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO prediction_results "
                        "(prediction_id, outcome, actual_score_home, actual_score_away, settled_by) "
                        "VALUES (?, ?, ?, ?, 'auto')",
                        (row["prediction_id"], outcome, row["home_score"], row["away_score"])
                    )
                    db.execute(
                        "UPDATE predictions SET status = ?, settled_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                        (outcome, row["prediction_id"])
                    )
                    db.commit()
                settled += 1

        logger.info(f"Auto-settled {settled} predictions")
        return {"settled": settled}

    # ── Subscription Expiry ─────────────────────────────────────────────────

    def check_subscription_expiry(self) -> Dict[str, Any]:
        """Expire lapsed subscriptions."""
        expired = self._subscription_mgr.expire_check()
        return {"expired": expired}

    # ── Odds Group Refresh ──────────────────────────────────────────────────

    def refresh_odds_groups(self) -> Dict[str, Any]:
        """Refresh active odds groups.

        Expires groups older than 24 hours with no activity,
        generates new groups for each risk level if needed.
        """
        with get_db() as db:
            # Expire old groups
            yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            cursor = db.execute(
                "UPDATE odds_groups SET status = 'expired' WHERE status = 'active' AND created_at < ?",
                (yesterday,)
            )
            expired = cursor.rowcount

            # Count active groups
            active_count = db.execute(
                "SELECT COUNT(*) as cnt FROM odds_groups WHERE status = 'active'"
            ).fetchone()["cnt"]
            db.commit()

        # Generate new groups if needed (minimum 5 active per risk level)
        generated = 0
        if active_count < 10:
            odds_engine = OddsEngine(self._prediction_engine)
            risk_levels = ["conservative", "moderate", "aggressive"]
            targets = [5, 10, 50]

            for risk, target in zip(risk_levels, targets):
                try:
                    result = odds_engine.generate(
                        target_odds=float(target),
                        risk_level=risk,
                        min_selections=2,
                        max_selections=8,
                    )
                    if result.selections:
                        with get_db() as db:
                            cursor = db.execute("""
                                INSERT INTO odds_groups (
                                    target_odds, label, risk_level, combined_odds,
                                    estimated_probability, average_confidence,
                                    num_selections, status
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                            """, (
                                result.target_odds,
                                result.label,
                                result.risk_level,
                                result.combined_odds,
                                result.estimated_probability,
                                result.average_confidence,
                                len(result.selections),
                            ))
                            group_id = cursor.lastrowid
                            for i, sel in enumerate(result.selections):
                                pred_id = _ensure_prediction_saved(db, sel)
                                db.commit()  # flush so FK is visible
                                db.execute(
                                    "INSERT INTO odds_group_selections "
                                    "(odds_group_id, prediction_id, sort_order) VALUES (?, ?, ?)",
                                    (group_id, pred_id, i + 1)
                                )
                            db.commit()
                        generated += 1
                except Exception as e:
                    logger.warning(f"Odds group refresh failed for {risk} {target}: {e}", exc_info=True)

        logger.info(f"Odds groups: {expired} expired, {generated} generated")
        return {"expired": expired, "generated": generated, "active_remaining": active_count}

    # ── Performance Metrics ─────────────────────────────────────────────────

    def update_performance_metrics(self) -> Dict[str, Any]:
        """Refresh performance metrics table."""
        self._perf_tracker.update_metrics_table()

        # Also compute daily metrics
        metrics = self._perf_tracker.get_metrics(period="daily")
        return {"win_rate": metrics.get("win_rate", 0), "total": metrics.get("total_predictions", 0)}

    # ── Run All Workers ─────────────────────────────────────────────────────

    def run_cycle(self) -> Dict[str, Any]:
        """Run the full automation cycle.

        Called from the Kai scheduler loop.
        """
        init_db()  # Ensure DB exists

        results = {}

        # Check subscriptions first (always run)
        try:
            results["subscriptions"] = self.check_subscription_expiry()
        except Exception as e:
            results["subscriptions_error"] = str(e)

        # Refresh events from external data providers (interval-gated)
        try:
            results["events_refresh"] = self.refresh_events()
        except Exception as e:
            results["events_refresh_error"] = str(e)

        # Sync odds and results from external data providers (interval-gated)
        try:
            results["odds_sync"] = self.refresh_sync()
        except Exception as e:
            results["odds_sync_error"] = str(e)

        # Auto-settle finished events
        try:
            results["settlement"] = self.auto_settle_finished()
        except Exception as e:
            results["settlement_error"] = str(e)

        # Generate daily predictions at configured time
        try:
            results["predictions"] = self.generate_daily_predictions()
        except Exception as e:
            results["predictions_error"] = str(e)

        # Refresh odds groups
        try:
            results["odds_groups"] = self.refresh_odds_groups()
        except Exception as e:
            results["odds_groups_error"] = str(e)

        # Update performance
        try:
            results["performance"] = self.update_performance_metrics()
        except Exception as e:
            results["performance_error"] = str(e)

        logger.info(f"Worker cycle complete: {results}")
        return results

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_markets_for_sport(self, db, sport_key: str) -> List[str]:
        """Get available market types for a sport from the database."""
        # Return predefined market types per sport
        markets_map = {
            "football": ["match_result", "double_chance", "over_under", "btts"],
            "basketball": ["match_result", "over_under"],
            "tennis": ["match_result", "over_under"],
            "baseball": ["match_result", "over_under"],
            "ice_hockey": ["match_result", "over_under"],
            "american_football": ["match_result", "over_under"],
        }
        return markets_map.get(sport_key, ["match_result"])

    @staticmethod
    def _determine_outcome(
        market_type: str,
        selection: str,
        home_score: int,
        away_score: int,
    ) -> Optional[str]:
        """Determine prediction outcome from actual scores."""
        selection_lower = selection.lower()

        if market_type == "match_result":
            if home_score > away_score:
                winner = "home"
            elif away_score > home_score:
                winner = "away"
            else:
                winner = "draw"
            return "won" if selection_lower == winner else ("push" if selection_lower == "draw" else "lost")

        elif market_type == "double_chance":
            if home_score > away_score and selection_lower in ("1x", "12", "home"):
                return "won"
            elif away_score > home_score and selection_lower in ("x2", "12", "away"):
                return "won"
            elif home_score == away_score and selection_lower in ("1x", "x2"):
                return "won"
            return "lost"

        elif market_type == "draw_no_bet":
            if home_score == away_score:
                return "push"
            if home_score > away_score and selection_lower == "home":
                return "won"
            if away_score > home_score and selection_lower == "away":
                return "won"
            return "lost"

        elif market_type == "over_under":
            total = home_score + away_score
            try:
                line = float(market_type.split("_")[-1]) if "_" in market_type else 2.5
            except ValueError:
                line = 2.5
            is_over = total > line
            if (is_over and selection_lower == "over") or (not is_over and selection_lower == "under"):
                return "won"
            return "lost"

        elif market_type == "btts":
            both_scored = home_score > 0 and away_score > 0
            if (both_scored and selection_lower == "yes") or (not both_scored and selection_lower == "no"):
                return "won"
            return "lost"

        # Default: can't determine
        return None


# ── Scheduler Integration ────────────────────────────────────────────────────

# Singleton instance for the Kai scheduler to call
_worker_instance: Optional[KaiBettingWorkers] = None


def get_workers() -> KaiBettingWorkers:
    """Get or create the singleton worker instance."""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = KaiBettingWorkers()
    return _worker_instance


def run_betting_cycle() -> Dict[str, Any]:
    """Entry point for Kai scheduler integration.

    Import and call this from core/scheduler.py:
        from core.kai_betting.workers import run_betting_cycle
        betting_results = run_betting_cycle()
    """
    return get_workers().run_cycle()
